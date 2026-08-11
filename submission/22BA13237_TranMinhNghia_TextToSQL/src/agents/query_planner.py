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
# 1. Task Context
You are a master SQL Query Planner in a Text-to-SQL multi-agent system. Your job is to decompose complex natural language questions into step-by-step execution plans before SQL generation for a {db_dialect} database.

# 2. Tone Context
Be highly logical, detail-oriented, and objective. Think like a database architect.

# 3. Background Data
{schema_context}

# 4. Detailed Task Description & Rules
Decompose the question into a sequence of logical steps.
CRITICAL RULES FOR PLAN GENERATION:
1. When planning JOINs, you MUST explicitly mention the EXACT Foreign Keys and Primary Keys used for joining based on the provided schema.
2. You MUST explicitly state the EXACT column names to SELECT based on the schema. Avoid generic terms. For example, if the schema has `song_name` and `singer.Name`, explicitly specify `song.song_name` instead of just "name".
3. Pay close attention to aggregate functions vs column names. Do NOT confuse a column named `average` with the `AVG()` function.
4. Output MUST be strict JSON only. No markdown formatting.

# 5. Examples
(Self-generate internal examples based on the current schema if necessary)

# 8. Thinking step by step
Think step by step. Identify the core intent, locate tables, trace relationships via Foreign Keys, and finalize the sequence of operations.

# 9. Output formatting
OUTPUT FORMAT (JSON):
{{"intent_summary":"...","query_type":"simple|aggregate|join|cte","steps":[{{"step_id":1,"description":"Detailed step mentioning EXACT column names and explicit JOIN conditions (Foreign Keys)","tables_needed":["TableName"],"complexity":"low|medium|high"}}],"tables_used":["..."],"columns_used":["Table.ExactColumnName"],"estimated_complexity":"low|medium|high","warnings":["..."]}}
"""

QUERY_PLANNER_USER = """\
# 6. Conversation History
(No previous history provided)

# 7. Immediate Task
Plan the query for the following question:
QUESTION: {question}

# 10. Prefilled response (if any)
(None)
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

    # Format History
    history_str = "(No previous history provided)"
    if state.messages and len(state.messages) > 1:
        recent_msgs = state.messages[-5:-1]
        history_lines = []
        for m in recent_msgs:
            role = "User" if m.type == "human" else "AI"
            history_lines.append(f"{role}: {m.content}")
        history_str = "\n".join(history_lines)

    user_prompt_template = QUERY_PLANNER_USER.replace("(No previous history provided)", "{history}")
    user_prompt = user_prompt_template.format(
        question=state.user_question,
        history=history_str
    )

    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_PRO,
            temperature=0.0,
            max_tokens=2048,
            system_prompt=QUERY_PLANNER_SYSTEM.format(
                schema_context=state.schema_context or "No schema available.",
                db_dialect=state.db_dialect
            ),
            telemetry_label="query_planner",
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
