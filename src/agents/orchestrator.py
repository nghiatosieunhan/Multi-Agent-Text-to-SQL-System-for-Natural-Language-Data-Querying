"""
Orchestrator Agent — orchestrates the entire pipeline.
The central "brain" of the multi-agent system.
"""
import json
import re
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config
from src.rag.schema_indexer import get_schema_context_for_query

log = structlog.get_logger("orchestrator")

ORCHESTRATOR_SYSTEM = """
# 1. Task Context
You are an expert SQL Orchestrator in a multi-agent Text-to-SQL system. Your job is to analyze user questions and route them to the appropriate agent.

# 2. Tone Context
Be highly analytical, precise, and objective. You do not talk, you only output structured data.

# 3. Background Data
{schema_context}

# 4. Detailed Task Description & Rules
Analyze the user question. Route it to the correct agent based on these routing rules:
- ambiguous: The question is completely unrelated to the database or uses undefined abbreviations/ambiguous terms. Ask user for clarification.
- simple: 1 table, basic WHERE/LIMIT clauses → sql_generator
- aggregate: Uses COUNT/SUM/AVG → query_planner
- join: Multi-table joins → query_planner
- complex: CTE, window functions, subqueries → query_planner

CRITICAL RULES:
- Only SELECT queries are permitted. No DROP/INSERT/UPDATE/DELETE.
- Output MUST be strict JSON only. No markdown fences.

# 5. Examples
Question: "Cho biết doanh thu" -> {{"intent_type": "ambiguous", "confidence": 0.9, "reasoning": "Không rõ doanh thu của sản phẩm, tháng, hay năm nào."}}

# 8. Thinking step by step
Think step by step about the complexity of the query, table relationships, and ambiguity before making a routing decision.

# 9. Output formatting
OUTPUT FORMAT (JSON):
{{"intent_type":"simple|aggregate|join|complex|ambiguous","confidence":0.0-1.0,"reasoning":"..."}}
"""

ORCHESTRATOR_USER_PROMPT = """\
# 6. Conversation History
(No history provided for this turn)

# 7. Immediate Task
Analyze this user question and output the JSON routing decision:
QUESTION: {question}

# 10. Prefilled response (if any)
(None)
"""


def _safe_json_parse(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    # Replace literal newlines to prevent json.loads Unterminated string errors
    text = text.replace('\n', ' ')
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise


def _retrieve_schema(
    question: str,
    db_path: str = None,
    pruning_mode: str = "auto",
) -> str:
    """Retrieve schema context for question (ChromaDB or fallback)."""
    try:
        from src.rag.schema_indexer import get_schema_context_for_query
        db = None
        if db_path:
            from src.db import get_db_manager
            db = get_db_manager(db_path)
            
        context = get_schema_context_for_query(
            question,
            db=db,
            top_k=6,
            pruning_mode=pruning_mode,
        )
        if not context:
            return "No schema context available."
        return context
    except Exception as e:
        return ""


def orchestrator_node(state: AgentState) -> AgentState:
    """
    Node: Orchestrator
    - Analyze question
    - Decide whether to use cache
    - Retrieve schema context
    - Route to the next agent
    """
    log.info("orchestrator_run", question=state.user_question[:80])

    cache_enabled = state.evaluation_options.get("cache_enabled", True)
    if cache_enabled and not state.cache_checked:
        from src.memory.semantic_cache import get_semantic_cache
        cache = get_semantic_cache()
        try:
            cached_data = cache.get(
                state.user_question,
                namespace=state.current_db_path,
            )
        except TypeError:
            cached_data = cache.get(state.user_question)
        state.cache_checked = True
        
        if cached_data:
            cached_result, cached_sql = cached_data
            log.info("orchestrator_cache_hit", question=state.user_question[:50])
            state.cache_hit = True
            state.cached_result = cached_result
            state.generated_sql = cached_sql
            state.next_agent = "result_formatter"
            return state

    # Step 2: Retrieve schema context
    if state.override_schema_context:
        schema_context = state.override_schema_context
    else:
        schema_context = _retrieve_schema(
            state.user_question,
            state.current_db_path,
            pruning_mode=state.evaluation_options.get("schema_pruning_mode", "auto"),
        )
    state.schema_context = schema_context

    # Step 3: Format History
    history_str = "(No history provided for this turn)"
    if state.messages and len(state.messages) > 1: # Ignore if it's only the current question
        # Extract last 4 messages for context
        recent_msgs = state.messages[-5:-1]
        history_lines = []
        for m in recent_msgs:
            role = "User" if m.type == "human" else "AI"
            history_lines.append(f"{role}: {m.content}")
        history_str = "\n".join(history_lines)
    
    # Step 4: LLM Decision
    user_prompt_template = ORCHESTRATOR_USER_PROMPT.replace("(No history provided for this turn)", "{history}")
    user_prompt = user_prompt_template.format(
        question=state.user_question,
        history=history_str
    )

    raw_response = ""
    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_PRO,
            temperature=config.ORCHESTRATOR_TEMPERATURE,
            max_tokens=1024,
            system_prompt=ORCHESTRATOR_SYSTEM.format(schema_context=state.schema_context),
            telemetry_label="orchestrator",
        )

        # Parse JSON response (handle markdown code blocks)
        decision = _safe_json_parse(raw_response)

        state.intent_type = decision.get("intent_type", "simple")
        state.intent_confidence = decision.get("confidence", 0.0)
        state.orchestrator_reasoning = decision.get("reasoning", "")
        state.current_step = "orchestrator_decided"
        
        if state.intent_type == "ambiguous":
            state.plan_needed = False
            state.next_agent = "result_formatter"
        else:
            state.plan_needed = (
                state.evaluation_options.get("planner_enabled", True)
                and state.intent_type in ("join", "complex", "cte", "subquery", "aggregate")
            )
            # Route directly: plan_needed=True → query_planner, else → sql_generator
            state.next_agent = "query_planner" if state.plan_needed else "sql_generator"

        log.info(
            "orchestrator_decision",
            intent=state.intent_type,
            confidence=state.intent_confidence,
            next=state.next_agent,
        )

    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("orchestrator_json_parse_failed", raw=repr(raw_response[:200]), error=str(exc))
        state.intent_type = "simple"
        state.plan_needed = False
        state.next_agent = "sql_generator"

    except Exception as e:
        log.error("orchestrator_error", error=str(e))
        state.error = f"Orchestrator error: {e}"
        state.next_agent = "error"

    return state
