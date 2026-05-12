"""
Result Formatter Agent — format kết quả thành câu trả lời dễ hiểu.
"""
import json
import structlog
from src.agents.state import AgentState
from src.agents.groq_llm import invoke
from src.config import config

log = structlog.get_logger("formatter")

RESULT_FORMATTER_SYSTEM = """
OUTPUT: Strict JSON:
{{"summary":"...","detailed_answer":"...","insights":["..."],"visualization":{{"recommended":true|false,"chart_type":"bar|line|pie|table"}}}}
"""

RESULT_FORMATTER_USER = """\
QUESTION: {question}
SQL RESULT: {sql_result}
"""


def result_formatter_node(state: AgentState) -> AgentState:
    """
    Node: ResultFormatter
    - Tạo câu trả lời tự nhiên từ kết quả SQL
    - Đề xuất visualization
    - Trả final result
    """
    log.info("formatter_run", row_count=state.query_result.get("row_count", 0) if state.query_result else 0)

    if state.cache_hit and state.cached_result:
        # Cache hit — vẫn format nhưng đánh dấu cache
        result_dict = state.cached_result
    elif state.query_result:
        result_dict = state.query_result
    else:
        result_dict = {"rows": [], "row_count": 0, "columns": [], "sql": ""}

    # Build result string cho prompt
    if result_dict.get("rows"):
        rows_preview = result_dict["rows"][:10]
        rows_str = json.dumps(rows_preview, ensure_ascii=False, default=str)
        result_summary = f"Kết quả: {result_dict['row_count']} dòng. Dữ liệu: {rows_str}"
    else:
        result_summary = "Không có dữ liệu trả về (0 dòng)."

    user_prompt = RESULT_FORMATTER_USER.format(
        question=state.user_question,
        sql_result=result_summary,
    )

    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL,
            temperature=0.2,
            max_tokens=1024,
            system_prompt=RESULT_FORMATTER_SYSTEM,
        )

        try:
            formatted = json.loads(raw_response)
        except json.JSONDecodeError:
            # Fallback: trả raw result
            formatted = {
                "summary": f"Kết quả: {result_dict.get('row_count', 0)} dòng.",
                "detailed_answer": raw_response or result_summary,
                "insights": [],
                "visualization": {"recommended": False, "reason": "No specific insight to visualize."},
                "row_count": result_dict.get("row_count", 0),
                "execution_note": "Result served" + (" from cache" if state.cache_hit else ""),
            }

        formatted["sql"] = result_dict.get("sql", state.generated_sql)
        formatted["execution_time_ms"] = state.execution_time_ms
        formatted["from_cache"] = state.cache_hit
        formatted["columns"] = result_dict.get("columns", [])
        formatted["rows"] = result_dict.get("rows", [])

        state.formatted_answer = formatted
        state.current_step = "formatted"
        state.next_agent = "finish"

        log.info(
            "formatter_complete",
            from_cache=state.cache_hit,
            row_count=formatted.get("row_count", 0),
        )

    except Exception as e:
        log.error("formatter_error", error=str(e))
        # Fallback response
        state.formatted_answer = {
            "summary": f"Kết quả: {result_dict.get('row_count', 0)} dòng.",
            "detailed_answer": str(state.query_result) if state.query_result else "No data.",
            "insights": [],
            "visualization": {"recommended": False, "reason": "Formatter error"},
            "sql": result_dict.get("sql", ""),
            "execution_time_ms": state.execution_time_ms,
            "from_cache": state.cache_hit,
            "columns": result_dict.get("columns", []),
            "rows": result_dict.get("rows", []),
        }
        state.next_agent = "finish"

    return state
