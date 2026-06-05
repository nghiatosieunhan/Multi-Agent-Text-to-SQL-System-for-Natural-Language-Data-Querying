"""
Query Planner Agent — analyzes and plans complex SQL queries.
"""
import json
import re
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config

log = structlog.get_logger("query_planner")

QUERY_PLANNER_SYSTEM = """
{schema_context}

CRITICAL RULES FOR PLAN GENERATION:
1. When planning JOINs, you MUST explicitly mention the EXACT Foreign Keys and Primary Keys used for joining based on the provided schema.
2. You MUST explicitly state the EXACT column names to SELECT based on the schema. Avoid generic terms. For example, if the schema has `song_name` and `singer.Name`, explicitly specify `song.song_name` instead of just "name".
3. Pay close attention to aggregate functions vs column names. Do NOT confuse a column named `average` with the `AVG()` function.

OUTPUT: Strict JSON only:
{{"intent_summary":"...","query_type":"simple|aggregate|join|cte","steps":[{{"step_id":1,"description":"Detailed step mentioning EXACT column names and explicit JOIN conditions (Foreign Keys)","tables_needed":["TableName"],"complexity":"low|medium|high"}}],"tables_used":["..."],"columns_used":["Table.ExactColumnName"],"estimated_complexity":"low|medium|high","warnings":["..."]}}
"""

QUERY_PLANNER_USER = """\
QUESTION: {question}
"""


def _safe_json_parse(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks and stray text."""
    import json as json_module
    # Strip markdown code fences
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    # Replace literal newlines to prevent json.loads Unterminated string errors
    text = text.replace('\n', ' ')
    
    try:
        return json_module.loads(text)
    except json_module.JSONDecodeError:
        # Try to extract JSON object from text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json_module.loads(match.group())
            except json_module.JSONDecodeError:
                pass
        raise


def query_planner_node(state: AgentState) -> AgentState:
    """
    Node: QueryPlanner
    - Chỉ chạy khi query phức tạp (plan_needed = True)
    - Tạo execution plan chi tiết
    - Trả về plan để SQL Generator sử dụng
    """
    log.info("query_planner_run", question=state.user_question[:80])

    user_prompt = QUERY_PLANNER_USER.format(
        question=state.user_question,
    )

    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_PRO,
            temperature=0.0,
            max_tokens=2048,
            system_prompt=QUERY_PLANNER_SYSTEM.format(schema_context=state.schema_context or "No schema available."),
        )

        plan = _safe_json_parse(raw_response)
        state.plan = plan
        state.tables_identified = plan.get("tables_used", [])
        state.columns_identified = plan.get("columns_used", [])
        state.current_step = "plan_generated"
        state.next_agent = "sql_generator"

        log.info(
            "plan_generated",
            query_type=plan.get("query_type", "unknown"),
            complexity=plan.get("estimated_complexity", "unknown"),
            tables=state.tables_identified,
        )

    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("plan_parse_failed", raw=raw_response[:200], error=str(exc))
        # Fallback: simple plan
        state.plan = {
            "intent_summary": state.user_question,
            "query_type": "simple",
            "steps": [{"step_id": 1, "description": "Simple SELECT", "tables_needed": [], "complexity": "low"}],
            "tables_used": [],
            "columns_used": [],
            "estimated_complexity": "low",
        }
        state.next_agent = "sql_generator"

    except Exception as e:
        log.error("query_planner_error", error=str(e))
        state.error = f"QueryPlanner error: {e}"
        state.next_agent = "error"

    return state
