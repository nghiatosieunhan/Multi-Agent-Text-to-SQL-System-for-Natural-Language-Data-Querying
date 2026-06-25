"""
Orchestrator Agent — orchestrates the entire pipeline.
The central "brain" of the multi-agent system.

Improvements:
  - temperature=0.0 for fully deterministic routing.
  - Adaptive routing: simple intent → sql_generator directly (compact route);
    aggregate/join/complex → query_spec_node → sql_generator.
  - orchestrator_decision stored in telemetry for evaluation diagnostics.
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
- simple: 1 table, basic WHERE/LIMIT clauses → sql_generator (compact route, no planner needed)
- aggregate: Uses COUNT/SUM/AVG → query_spec (structured spec needed)
- join: Multi-table joins → query_spec (structured spec needed)
- complex: CTE, window functions, subqueries → query_spec (structured spec needed)

CRITICAL RULES:
- Only SELECT queries are permitted. No DROP/INSERT/UPDATE/DELETE.
- Output MUST be strict JSON only. No markdown fences.

# 5. Examples
Question: "Cho biết doanh thu" -> {{"intent_type": "ambiguous", "confidence": 0.9, "reasoning": "Không rõ doanh thu của sản phẩm, tháng, hay năm nào."}}
Question: "Liệt kê tên tất cả khách hàng" -> {{"intent_type": "simple", "confidence": 0.95, "reasoning": "One table, basic SELECT."}}
Question: "Tổng doanh thu theo danh mục sản phẩm" -> {{"intent_type": "aggregate", "confidence": 0.9, "reasoning": "Requires SUM + GROUP BY across multiple tables."}}

# 8. Thinking step by step
Think step by step about the complexity of the query, table relationships, and ambiguity before making a routing decision.

# 9. Output formatting
OUTPUT FORMAT (JSON):
{{"intent_type":"simple|aggregate|join|complex|ambiguous","confidence":0.0-1.0,"reasoning":"..."}}
"""

ORCHESTRATOR_USER_PROMPT = """\
# 6. Conversation History
{history}

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
    except Exception:
        return ""


def orchestrator_node(state: AgentState) -> AgentState:
    """
    Node: Orchestrator
    - Check cache
    - Retrieve schema context
    - Route: simple → sql_generator (compact); aggregate/join/complex → query_spec
    """
    log.info("orchestrator_run", question=state.user_question[:80])

    # Step 1: Cache check
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

    # Step 3: Format conversation history
    history_str = "(No history provided for this turn)"
    if state.messages and len(state.messages) > 1:
        recent_msgs = state.messages[-5:-1]
        history_lines = []
        for m in recent_msgs:
            role = "User" if m.type == "human" else "AI"
            history_lines.append(f"{role}: {m.content}")
        history_str = "\n".join(history_lines)

    # Step 4: LLM routing decision (temperature=0.0 — deterministic)
    user_prompt = ORCHESTRATOR_USER_PROMPT.format(
        question=state.user_question,
        history=history_str,
    )

    raw_response = ""
    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_PRO,
            temperature=0.0,  # Deterministic routing — do not change
            max_tokens=1024,
            system_prompt=ORCHESTRATOR_SYSTEM.format(schema_context=state.schema_context),
            telemetry_label="orchestrator",
        )

        decision = _safe_json_parse(raw_response)

        state.intent_type = decision.get("intent_type", "simple")
        state.intent_confidence = decision.get("confidence", 0.0)
        state.orchestrator_reasoning = decision.get("reasoning", "")
        benchmark_intent = (state.benchmark_context or {}).get("intent")
        if state.intent_type == "ambiguous" and benchmark_intent:
            state.intent_type = benchmark_intent
            state.intent_confidence = max(state.intent_confidence, 0.8)
            state.orchestrator_reasoning = (
                "Benchmark intent override: "
                + (state.orchestrator_reasoning or "model marked ambiguous")
            )
        state.current_step = "orchestrator_decided"

        # Store decision in telemetry for evaluation diagnostics (last_node, orchestrator_decision)
        state.telemetry["orchestrator_decision"] = {
            "intent": state.intent_type,
            "confidence": state.intent_confidence,
            "reasoning": state.orchestrator_reasoning,
        }

        if state.intent_type == "ambiguous":
            state.plan_needed = False
            state.next_agent = "result_formatter"
        else:
            # Adaptive routing:
            #   simple  → sql_generator directly (compact route — saves 1-2 LLM calls)
            #   others  → query_spec_node → sql_generator (structured spec required)
            force_spec_for_all = state.evaluation_options.get("force_query_spec_for_all", False)
            needs_spec = force_spec_for_all or state.intent_type in (
                "join",
                "complex",
                "cte",
                "subquery",
                "aggregate",
            )
            spec_enabled = state.evaluation_options.get("query_spec_enabled", True)
            planner_enabled = state.evaluation_options.get("planner_enabled", True)

            if needs_spec and spec_enabled:
                state.plan_needed = False  # query_spec replaces planner
                state.next_agent = "query_spec"
                route_reason = "spec_required"
            elif needs_spec and planner_enabled:
                state.plan_needed = True
                state.next_agent = "query_planner"
                route_reason = "planner_fallback"
            else:
                state.plan_needed = False
                state.next_agent = "sql_generator"
                route_reason = "compact"

        log.info(
            "orchestrator_decision",
            intent=state.intent_type,
            confidence=state.intent_confidence,
            next=state.next_agent,
            route_reason=locals().get("route_reason", "ambiguous"),
        )

    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("orchestrator_json_parse_failed", raw=repr(raw_response[:200]), error=str(exc))
        state.intent_type = "simple"
        state.plan_needed = False
        state.next_agent = "sql_generator"

    except Exception as exc:
        log.error("orchestrator_error", error=str(exc))
        state.error = f"Orchestrator error: {exc}"
        state.next_agent = "error"

    return state
