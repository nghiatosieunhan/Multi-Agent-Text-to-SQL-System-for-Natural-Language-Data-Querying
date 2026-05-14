"""
SQL Generator Agent — sinh SQL query từ câu hỏi tự nhiên.
FIXED: bỏ alias cứng, thêm column hints rõ ràng.
"""
import json
import re
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config
from src.rag.few_shot_retriever import FewShotRetriever
log = structlog.get_logger("sql_generator")


def _extract_sql(text: str) -> str:
    """Extract SQL từ JSON response hoặc plain text."""
    sql = ""
    try:
        obj = json.loads(text)
        if "sql" in obj and obj["sql"]:
            temp_sql = obj["sql"].strip()
            if temp_sql.upper().startswith("SELECT"):
                sql = temp_sql
    except (json.JSONDecodeError, TypeError):
        pass

    if not sql:
        patterns = [
            r"```sql\s*(.*?)\s*```",
            r"```\s*(SELECT.*?;)\s*```",
            r"(SELECT\s+.*?;)",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            if matches:
                sql = matches[0].strip()
                break

    if not sql and text.strip().upper().startswith("SELECT"):
        sql = text.strip()
        
    # Xử lý trường hợp LLM trả về chuỗi SQL có chứa backslash escape như \"Order Details\"
    if sql:
        sql = sql.replace('\\"', '"')
    return sql


def _validate_dangerous(sql: str) -> tuple[bool, list]:
    """Chỉ chấp nhận SELECT."""
    if not sql:
        return False, ["No SQL generated"]
    upper = sql.upper()
    dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    issues = [kw for kw in dangerous if re.search(r'\b' + kw + r'\b', upper)]
    return len(issues) == 0, issues


def _build_fallback_sql(question: str, state: AgentState) -> str:
    """Fallback SQL generator đơn giản hóa khi LLM fail."""
    q = question.lower()
    tables_map = {
        "album": "Album", "artist": "Artist", "track": "Track",
        "bai hat": "Track", "nghe si": "Artist", "khach hang": "Customer",
        "invoice": "Invoice", "hoa don": "Invoice", "the loai": "Genre",
        "genre": "Genre", "playlist": "Playlist", "nhan vien": "Employee",
        "employee": "Employee", "mediatype": "MediaType", "line": "InvoiceLine",
    }

    detected_tables = [v for k, v in tables_map.items() if k in q]
    if not detected_tables:
        detected_tables = ["Track"]

    table = detected_tables[0]

    if "dem" in q or "so luong" in q or "tong so" in q:
        return f"SELECT COUNT(*) AS total FROM {table};"
    if "tat ca" in q or "danh sach" in q:
        return f"SELECT * FROM {table} LIMIT 20;"
    return f"SELECT * FROM {table} LIMIT 10;"


# ── System prompt (dynamic — schema injected via user prompt) ───────────────
SYSTEM_PROMPT_TEMPLATE = """You are an expert SQLite query generator.

DATABASE SCHEMA:
{schema}

CRITICAL RULES:
1. ONLY SELECT — never DROP/INSERT/UPDATE/DELETE
2. SQLite syntax: LIMIT 10 (not TOP 10), ROUND(col,2), strftime('%Y',date)
3. Define your own table aliases — do NOT use pre-defined aliases
4. Always use explicit column names from the schema above
5. Escape single quotes: '' not \\'  (e.g. WHERE Name = '90''s Music')
6. LIMIT must be integer: LIMIT 10 (not LIMIT 1st)
7. Always end SQL with semicolon

OUTPUT: strict JSON only — no markdown, no explanation outside JSON:
{{"sql":"SELECT ...;","confidence":0.9,"reasoning":"brief explanation"}}
"""

SQL_GENERATOR_RULES = """
CRITICAL SQL RULES FOR MULTI-DATABASE (SPIDER):
1. EVIDENCE IS KING: If "BUSINESS RULES / EVIDENCE" are provided in the prompt, you MUST strictly follow them. Use the exact formulas, conditions, and calculations specified in the evidence. Do NOT use your own assumptions if evidence explicitly defines a business logic.
2. ONLY SELECT — reject DROP/INSERT/UPDATE/DELETE.
3. EXACT COLUMN MATCHING: You MUST strictly use the exact Table and Column names provided in the SCHEMA. 
   - TRAP ALERT: If the question asks for a specific attribute (e.g., "song name"), do NOT default to a generic "Name" column if a more specific column like "song_name" exists.
4. COLUMN ORDER: If the question explicitly asks to "list A and B", your SELECT clause MUST output the columns in that exact order: `SELECT A, B`.
5. TOP-1 AGGREGATION (CRITICAL): If the question asks to calculate an aggregate (e.g., COUNT) for the "top 1", "highest", or "largest" entity, YOU MUST USE A SUBQUERY in the WHERE clause. 
   - BAD: `SELECT COUNT(id) FROM A JOIN B ORDER BY B.capacity DESC LIMIT 1`
   - GOOD: `SELECT COUNT(id) FROM A WHERE b_id = (SELECT id FROM B ORDER BY capacity DESC LIMIT 1)`
6. PRE-COMPUTED COLUMNS vs. FUNCTIONS: 
   - TRAP ALERT: If the question asks for "average", "total", or "sum", FIRST check if there is a column explicitly named "average", "total", or "sum" in the table. If such a column exists, SELECT IT DIRECTLY. Do NOT wrap other columns in AVG() or SUM() unless no such pre-computed column exists.
7. MANDATORY ALIASING: You MUST prefix EVERY column with its table alias to avoid ambiguity (e.g., use `T1.id`, `T2.name`).
8. FOREIGN KEYS: When using JOIN, examine the "Foreign Key" section to match the correct IDs. Do not join on names.
9. SQLITE SYNTAX: Use `ORDER BY col DESC LIMIT 1` (DO NOT use TOP 1 or LIMIT 1st).
10. End every query with a semicolon `;`.
"""


def _build_schema_text_for_prompt(schema_context: str) -> str:
    """
    Build schema text cho system prompt.
    Nếu schema_context đã có dạng text (từ schema indexer) → dùng trực tiếp.
    """
    if schema_context and schema_context.strip():
        return schema_context.strip()
    return (
        "No schema context available. "
        "Please infer the schema from the question and generate a safe query."
    )


def sql_generator_node(state: AgentState) -> AgentState:
    """Node: SQLGenerator với retry và fallback."""
    state.generation_attempts += 1
    few_shot_text = ""
    # 1. Lấy ví dụ mẫu (Few-shot)
    if not state.evidence:
        try:
            retriever = FewShotRetriever()
            examples = retriever.retrieve(state.user_question, k=3)
            few_shot_text = "DƯỚI ĐÂY LÀ CÁC VÍ DỤ MẪU TƯƠNG TỰ:\n"
            for i, ex in enumerate(examples):
                few_shot_text += f"Ví dụ {i+1}:\n- Câu hỏi: {ex['question']}\n- SQL: {ex['sql']}\n\n"
        except Exception as e:
            log.warning("few_shot_failed", error=str(e))

    # 2. Build schema và system prompt
    schema_text = _build_schema_text_for_prompt(state.schema_context)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text) + SQL_GENERATOR_RULES

    # 3. Kết hợp Plan và Ví dụ mẫu vào User Prompt
    plan_ctx = ""
    if state.plan:
        steps = "\n".join(f"Step {s['step_id']}: {s['description']}" for s in state.plan.get("steps", []))
        plan_ctx = f"EXECUTION PLAN:\n{steps}\n"

    # Xử lý evidence của BIRD
    evidence_ctx = f"BUSINESS RULES / EVIDENCE:\n{state.evidence}\n" if state.evidence else ""

    user_prompt = f"""{few_shot_text}
CÂU HỎI HIỆN TẠI: {state.user_question}
{evidence_ctx}
{plan_ctx}

LƯU Ý QUAN TRỌNG ĐỂ VƯỢ QUA ĐÁNH GIÁ (EVALUATION):
1. THEO SÁT VÍ DỤ: Hãy quan sát cấu trúc SELECT của các ví dụ tương tự. Nếu câu hỏi yêu cầu top N (top 5, top 10), thường cần SELECT thêm cột Aggregation (SUM, COUNT, v.v.).
2. NẾU YÊU CẦU HIỂN THỊ TÊN ĐẦY ĐỦ (FullName), hãy nối FirstName và LastName (VD: `FirstName || ' ' || LastName`).
3. TÊN BẢNG HOẶC CỘT CÓ KHOẢNG TRẮNG: Bắt buộc phải đặt trong dấu ngoặc kép (VD: `"Order Details"`). KHÔNG BAO GIỜ được viết dính liền (OrderDetails).
4. KIỂM TRA DISTINCT: Đọc kĩ xem câu hỏi có chữ "khác nhau" hay "riêng biệt" (distinct) không.
5. KIỂM TRA ROUND: Cân nhắc sử dụng ROUND nếu ví dụ yêu cầu, hoặc làm tròn khi tính giá trị tiền tệ.
6. KHI JOIN, cẩn thận tránh nhầm lẫn cột (ví dụ ShipRegion của Orders vs RegionDescription của Regions).
7. PROJECTION MATCHING (CỰC KỲ QUAN TRỌNG): Chỉ SELECT chính xác các cột mà câu hỏi yêu cầu. KHÔNG SELECT thừa (VD: hỏi Tên thì chỉ SELECT Name/Title, KHÔNG SELECT thêm ID). Đừng tự ý select bảng (*) nếu không được yêu cầu.
8. CHỌN ĐÚNG BẢNG/CỘT: Chú ý kỹ bảng nào chứa thông tin đúng nhất (Ví dụ hỏi "bài hát" thì dùng bảng Track thay vì InvoiceLine). Nếu câu hỏi nói "doanh thu", hãy chú ý các cột UnitPrice * Quantity.

Hãy dựa vào SCHEMA, BUSINESS RULES (nếu có) và cấu trúc của các ví dụ trên để viết SQL chính xác nhất. Chỉ trả về JSON."""

    raw = ""
    for attempt in range(2):
        try:
            raw = invoke(
                prompt=user_prompt,
                model=config.LLM_MODEL,
                temperature=0.0,
                max_tokens=1536,
                system_prompt=system_prompt,
            )
            break
        except Exception as e:
            log.warning("sql_gen_retry", attempt=attempt + 1, error=str(e))
            if attempt == 1:
                raw = ""

    sql = _extract_sql(raw)
    is_safe, issues = _validate_dangerous(sql)

    if not is_safe or not sql:
        fallback_sql = _build_fallback_sql(state.user_question, state)
        state.generated_sql = fallback_sql
        state.sql_confidence = 0.3
        state.next_agent = "executor"
        state.current_step = "sql_generated"
        log.warning("sql_gen_fallback_used", fallback=fallback_sql[:80])
        return state

    try:
        obj = json.loads(raw)
        state.sql_confidence = float(obj.get("confidence", 0.7))
        state.sql_reasoning = obj.get("reasoning", "")
    except (json.JSONDecodeError, TypeError):
        state.sql_confidence = 0.6

    state.generated_sql = sql
    state.next_agent = "executor"
    state.current_step = "sql_generated"

    log.info("sql_generated", sql=sql[:80], confidence=state.sql_confidence)
    return state
