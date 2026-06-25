"""
Result Formatter Agent — formats results into an easy-to-understand answer.
"""
import json
import re
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config

log = structlog.get_logger("formatter")

RESULT_FORMATTER_SYSTEM = """
# 1. Task Context
You are a Data Analyst and Result Formatter in a multi-agent system. Your job is to format raw SQL results into an easy-to-understand answer for non-technical users.

# 2. Tone Context
Be friendly, concise, and helpful. Use clear language.
CRITICAL: You MUST answer entirely in VIETNAMESE (Tiếng Việt). Do not use English for your final response.

# 3. Background Data
(No background schema needed for formatting)

# 4. Detailed Task Description & Rules
Analyze the question and the SQL result.
- The summary must be one natural Vietnamese sentence that directly answers the question.
- The detailed_answer must contain useful values from the returned rows, not just say that data exists.
- If the result is a list, present the most relevant rows as concise Markdown bullet points.
- In each bullet, bold the primary name or identifier, then show the most useful related values.
- If it's a single number, return a short sentence containing that number.
- Do not repeat every technical column when many columns are returned; prioritize human-readable fields.
- Recommend a visualization if applicable.
- DO NOT generate follow-up questions or suggestions (Có thể bạn quan tâm) to save generation time.

# 5. Example
For an employee and region list:
{
  "summary": "Dưới đây là danh sách các nhân viên và khu vực mà họ được phân công.",
  "detailed_answer": "Dựa trên dữ liệu, các nhân viên gồm:\\n\\n- **Nancy Davolio:** Miền Đông\\n- **Andrew Fuller:** Miền Đông\\n- **Janet Leverling:** Miền Nam",
  "insights": [
    "Có 3 nhân viên trong kết quả.",
    "Các nhân viên được phân công vào 2 khu vực.",
    "Miền Đông có nhiều nhân viên được phân công nhất."
  ],
  "visualization": {"recommended": true, "chart_type": "bar"}
}

# 8. Thinking step by step
Think step by step. Read the user's question, examine the rows returned, formulate a natural language response, and decide if a chart is appropriate.
Generate exactly 3 analytical insights based on the data.

# 9. Output formatting
OUTPUT FORMAT (Strict JSON):
{"summary":"...","detailed_answer":"...","insights":["Insight 1", "Insight 2", "Insight 3"],"visualization":{"recommended":true|false,"chart_type":"bar|line|pie|table"}}
"""

RESULT_FORMATTER_USER = """\
# 6. Conversation History
(No previous history provided)

# 7. Immediate Task
Format the result for the following question:
QUESTION: {question}
SQL RESULT: {sql_result}

# 10. Prefilled response (if any)
(None)
"""


def _safe_fallback_response(result_dict: dict, reason: str) -> dict:
    """Build a useful answer from result rows without exposing raw dictionaries."""
    row_count = result_dict.get("row_count", 0) or 0
    rows = result_dict.get("rows") or []
    columns = result_dict.get("columns") or []
    if rows:
        summary = f"Dưới đây là {row_count} kết quả phù hợp với yêu cầu của bạn."
        bullet_lines = []
        for row in rows[:10]:
            if isinstance(row, dict):
                items = [(str(key), value) for key, value in row.items()]
            elif isinstance(row, (list, tuple)):
                items = [
                    (str(columns[index]) if index < len(columns) else f"Cột {index + 1}", value)
                    for index, value in enumerate(row)
                ]
            else:
                items = [("Giá trị", row)]

            visible_items = [(key, value) for key, value in items if value not in (None, "")]
            if not visible_items:
                continue

            primary_key, primary_value = visible_items[0]
            details = [f"{key}: {value}" for key, value in visible_items[1:4]]
            line = f"- **{primary_value}**"
            if details:
                line += f" — {', '.join(details)}"
            bullet_lines.append(line)

        detailed_answer = "Dựa trên dữ liệu truy vấn:\n\n" + "\n".join(bullet_lines)
        if row_count > len(bullet_lines):
            detailed_answer += f"\n\n_Bảng bên dưới chứa đầy đủ {row_count} dòng dữ liệu._"

        insights = [
            f"Truy vấn trả về tổng cộng {row_count} dòng dữ liệu.",
            f"Phần trả lời đang hiển thị {min(len(rows), 10)} dòng đầu tiên để dễ theo dõi.",
            "Bạn có thể mở bảng dữ liệu bên dưới để xem toàn bộ các cột và giá trị.",
        ]
    else:
        summary = "Truy vấn chạy thành công nhưng không tìm thấy dữ liệu phù hợp."
        detailed_answer = "Bạn có thể kiểm tra lại điều kiện lọc hoặc thử diễn đạt câu hỏi cụ thể hơn."
        insights = []

    return {
        "summary": summary,
        "detailed_answer": detailed_answer,
        "insights": insights,
        "visualization": {"recommended": False, "reason": reason},
        "suggestions": [],
        "row_count": row_count,
        "execution_note": "Result served",
    }


def _parse_formatter_response(raw_response: str) -> dict | None:
    """Parse strict JSON even when the model wraps it in fences or extra text."""
    if not raw_response:
        return None

    text = raw_response.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    decoder = json.JSONDecoder()
    candidates = [text]
    first_brace = text.find("{")
    if first_brace > 0:
        candidates.append(text[first_brace:])

    for candidate in candidates:
        try:
            parsed, _ = decoder.raw_decode(candidate.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def result_formatter_node(state: AgentState) -> AgentState:
    """
    Node: ResultFormatter
    - Generates a natural language answer from SQL results
    - Recommends visualization
    - Returns final result
    """
    log.info("formatter_run", row_count=state.query_result.get("row_count", 0) if state.query_result else 0)

    if state.clarification_needed:
        clarification_msg = state.clarification_reason or "Vui lòng chọn rõ cơ sở dữ liệu cần truy vấn."
        state.formatted_answer = {
            "summary": "Cần chọn rõ cơ sở dữ liệu",
            "detailed_answer": clarification_msg,
            "insights": [],
            "visualization": {"recommended": False, "reason": "Database clarification needed."},
            "sql": "",
            "execution_time_ms": 0.0,
            "from_cache": False,
            "columns": [],
            "rows": [],
        }
        state.current_step = "clarification_requested"
        state.next_agent = "finish"
        return state

    if state.intent_type == "ambiguous":
        clarification_msg = "Xin lỗi, câu hỏi của bạn có chứa các từ khóa chưa rõ ràng hoặc không có trong cơ sở dữ liệu. Bạn có thể giải thích rõ hơn hoặc cung cấp thêm ngữ cảnh được không?"
        if state.orchestrator_reasoning:
            clarification_msg = f"Xin lỗi, tôi cần bạn làm rõ: {state.orchestrator_reasoning}"
            
        state.formatted_answer = {
            "summary": "Cần làm rõ câu hỏi",
            "detailed_answer": clarification_msg,
            "insights": [],
            "visualization": {"recommended": False, "reason": "Ambiguous question."},
            "sql": "",
            "execution_time_ms": 0.0,
            "from_cache": False,
            "columns": [],
            "rows": [],
        }
        state.current_step = "clarification_requested"
        state.next_agent = "finish"
        return state

    if state.cache_hit and state.cached_result:
        # Cache hit — format but mark as cached
        result_dict = state.cached_result
    elif state.query_result:
        result_dict = state.query_result
    else:
        result_dict = {"rows": [], "row_count": 0, "columns": [], "sql": ""}

    # Build result string for prompt
    if result_dict.get("rows"):
        rows_preview = result_dict["rows"][:10]
        rows_str = json.dumps(rows_preview, ensure_ascii=False, default=str)
        result_summary = f"Result: {result_dict['row_count']} rows. Data: {rows_str}"
    else:
        result_summary = "No data returned (0 rows)."

    user_prompt = RESULT_FORMATTER_USER.format(
        question=state.user_question,
        sql_result=result_summary,
    )

    if state.analysis_mode == "fast":
        formatted = _safe_fallback_response(result_dict, "Fast mode enabled")
        formatted["sql"] = result_dict.get("sql", state.generated_sql)
        formatted["execution_time_ms"] = state.execution_time_ms
        formatted["from_cache"] = state.cache_hit
        formatted["columns"] = result_dict.get("columns", [])
        formatted["rows"] = result_dict.get("rows", [])
        formatted["row_count"] = result_dict.get("row_count", 0)
        
        state.formatted_answer = formatted
        state.current_step = "formatting_skipped_fast_mode"
        state.next_agent = "finish"
        return state

    try:
        raw_response = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_FLASH,
            temperature=0.0,
            max_tokens=2048,
            system_prompt=RESULT_FORMATTER_SYSTEM,
            telemetry_label="result_formatter",
        )

        formatted = _parse_formatter_response(raw_response)
        if formatted is None:
            formatted = _safe_fallback_response(
                result_dict,
                "Formatter returned invalid JSON.",
            )

        formatted["sql"] = result_dict.get("sql", state.generated_sql)
        formatted["execution_time_ms"] = state.execution_time_ms
        formatted["from_cache"] = state.cache_hit
        formatted["columns"] = result_dict.get("columns", [])
        formatted["rows"] = result_dict.get("rows", [])
        formatted["row_count"] = result_dict.get("row_count", 0)

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
        state.formatted_answer = _safe_fallback_response(result_dict, "Formatter error")
        state.formatted_answer["sql"] = result_dict.get("sql", state.generated_sql)
        state.formatted_answer["execution_time_ms"] = state.execution_time_ms
        state.formatted_answer["from_cache"] = state.cache_hit
        state.formatted_answer["columns"] = result_dict.get("columns", [])
        state.formatted_answer["rows"] = result_dict.get("rows", [])
        state.next_agent = "finish"

    return state
