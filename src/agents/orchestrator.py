"""
Orchestrator Agent — điều phối toàn bộ pipeline.
Là "brain" trung tâm của multi-agent system.
"""
import json
import re
import structlog
from src.agents.state import AgentState
from src.agents.groq_llm import invoke
from src.config import config
from src.rag.schema_indexer import get_schema_context_for_query

log = structlog.get_logger("orchestrator")

ORCHESTRATOR_SYSTEM = """
{schema_context}

RULE: Only SELECT queries — no DROP/INSERT/UPDATE/DELETE.

TASK: Analyze user question → route to correct agent.

OUTPUT: Strict JSON only, no markdown:
{{"intent_type":"simple|aggregate|join|complex","confidence":0.0-1.0,"reasoning":"..."}}

RULES:
- simple (1 table, WHERE/LIMIT) → sql_generator
- aggregate (COUNT/SUM/AVG) → query_planner then sql_generator
- join/multi-table → query_planner → sql_generator
- complex/CTE/window → query_planner → sql_generator
"""

ORCHESTRATOR_USER_PROMPT = """\
QUESTION: {question}
"""


def _safe_json_parse(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
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


def _retrieve_schema(question: str) -> str:
    """Lấy schema context cho question (ChromaDB hoặc fallback)."""
    try:
        context = get_schema_context_for_query(question, top_k=6)
        if not context:
            return "No schema context available."
        return context
    except Exception as e:
        return ""


def orchestrator_node(state: AgentState) -> AgentState:
    """
    Node: Orchestrator
    - Phân tích câu hỏi
    - Quyết định có dùng cache không
    - Lấy schema context
    - Route đến agent tiếp theo
    """
    log.info("orchestrator_run", question=state.user_question[:80])

    if not state.cache_checked:
        # NOTE: orchestrator-level semantic cache disabled (embedding similarity
        # too loose — "số lượng album" matches "danh sách album" but returns wrong SQL)
        state.cache_checked = True

    # Step 2: Retrieve schema context
    schema_context = _retrieve_schema(state.user_question)
    state.schema_context = schema_context

    # Step 4: LLM Decision
    user_prompt = ORCHESTRATOR_USER_PROMPT.format(
        question=state.user_question,
    )

    raw_response = ""
    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL,
            temperature=config.ORCHESTRATOR_TEMPERATURE,
            max_tokens=1024,
            system_prompt=ORCHESTRATOR_SYSTEM.format(schema_context=state.schema_context),
        )

        # Parse JSON response (handle markdown code blocks)
        decision = _safe_json_parse(raw_response)

        state.intent_type = decision.get("intent_type", "simple")
        state.intent_confidence = decision.get("confidence", 0.0)
        state.orchestrator_reasoning = decision.get("reasoning", "")
        state.current_step = "orchestrator_decided"
        state.plan_needed = state.intent_type in ("join", "complex", "cte", "subquery", "aggregate")
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
