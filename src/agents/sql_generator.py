"""
SQL Generator Agent — generates SQL queries from natural language questions.
FIXED: removed hardcoded aliases, added clear column hints.
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
    """Trích xuất SQL từ response JSON hoặc Markdown."""
    sql = ""
    # Clean markdown blocks first
    cleaned_text = re.sub(r'^```(?:json|sql)?\s*', '', text.strip(), flags=re.MULTILINE)
    cleaned_text = re.sub(r'\s*```$', '', cleaned_text, flags=re.MULTILINE)
    cleaned_text = cleaned_text.strip()
    
    # Replace literal newlines with space to prevent json.loads from failing
    json_ready_text = cleaned_text.replace('\n', ' ')
    
    # Try parsing as JSON first
    try:
        obj = json.loads(json_ready_text)
        if "sql" in obj and obj["sql"]:
            temp_sql = obj["sql"].strip()
            if temp_sql.upper().startswith("SELECT") or temp_sql.upper().startswith("WITH"):
                sql = temp_sql
    except (json.JSONDecodeError, TypeError):
        # Fallback to fuzzy JSON parsing
        match = re.search(r'\{.*\}', json_ready_text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                if "sql" in obj and obj["sql"]:
                    temp_sql = obj["sql"].strip()
                    if temp_sql.upper().startswith("SELECT") or temp_sql.upper().startswith("WITH"):
                        sql = temp_sql
            except (json.JSONDecodeError, TypeError):
                pass

    if not sql:
        patterns = [
            r"```sql\s*(.*?)\s*```",
            r"```\s*((?:SELECT|WITH).*?;)\s*```",
            r"((?:SELECT|WITH)\s+.*?;)",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            if matches:
                sql = matches[0].strip()
                break

    if not sql and (cleaned_text.strip().upper().startswith("SELECT") or cleaned_text.strip().upper().startswith("WITH")):
        sql = cleaned_text.strip()
        
    # Handle cases where LLM returns SQL string containing backslash escapes like \"Order Details\"
    if sql:
        sql = sql.replace('\\"', '"')
    return sql


def _validate_dangerous(sql: str) -> tuple[bool, list]:
    """Only allow SELECT statements."""
    if not sql:
        return False, ["No SQL generated"]
    upper = sql.upper()
    dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    issues = [kw for kw in dangerous if re.search(r'\b' + kw + r'\b', upper)]
    return len(issues) == 0, issues


def _build_fallback_sql(question: str, state: AgentState) -> str:
    """Dynamic fallback SQL generator when LLM fails, using actual schema."""
    q = question.lower()
    
    # Lấy danh sách các bảng có thật từ context thay vì hardcode
    available_tables = []
    if state.schema_context:
        matches = re.findall(r'(?:Table:|CREATE\s+TABLE)\s+["\'`]?([a-zA-Z0-9_]+)["\'`]?', state.schema_context, re.IGNORECASE)
        available_tables = list(set(matches))
                
    if not available_tables:
        return "SELECT 'No tables found in schema';"

    # Tìm bảng có tên khớp với câu hỏi
    detected_tables = []
    for table in available_tables:
        clean_name = table.lower().replace("_", " ")
        if clean_name in q:
            detected_tables.append(table)
            
    # Nếu không tìm thấy, chọn bảng đầu tiên có thật trong Database
    table = detected_tables[0] if detected_tables else available_tables[0]

    if "dem" in q or "so luong" in q or "tong so" in q or "count" in q:
        return f'SELECT COUNT(*) AS total FROM "{table}";'
    if "tat ca" in q or "danh sach" in q or "all" in q:
        return f'SELECT * FROM "{table}" LIMIT 20;'
    return f'SELECT * FROM "{table}" LIMIT 10;'


# ── System prompt (dynamic — schema injected via user prompt) ───────────────
SYSTEM_PROMPT_TEMPLATE = """You are an expert SQLite query generator.

DATABASE SCHEMA:
{schema}

CRITICAL RULES:
1. ONLY SELECT — never DROP/INSERT/UPDATE/DELETE
2. STRICT WHITELISTING: DO NOT hallucinate table or column names. You MUST ONLY use the EXACT tables and columns defined in the schema above. If a user asks for 'dogs', find the closest valid table in the schema (e.g. 'pets').
3. AGGREGATION LOGIC: When calculating aggregates for superlatives (highest, lowest), prefer using `ORDER BY ... LIMIT 1` combined with JOINs, rather than subqueries in the WHERE clause, so that you can project the evidence column in the SELECT clause.
4. Define your own table aliases — do NOT use pre-defined aliases
5. Always use explicit column names from the schema above
6. Escape single quotes: '' not \\'  (e.g. WHERE Name = '90''s Music')
7. LIMIT must be integer: LIMIT 10 (not LIMIT 1st)
8. Always end SQL with semicolon

OUTPUT: strict JSON only — no markdown, no explanation outside JSON:
{{"sql":"SELECT ...;","confidence":0.9,"reasoning":"brief explanation"}}
"""

SQL_GENERATOR_RULES = """
CRITICAL SQL RULES FOR MULTI-DATABASE (SPIDER & ENTERPRISE):
1. EVIDENCE IS KING: If "BUSINESS RULES / EVIDENCE" are provided, strictly follow them. Do NOT use your own assumptions.
2. ONLY SELECT — reject DROP/INSERT/UPDATE/DELETE.
3. EXACT COLUMN MATCHING: Strictly use the exact Table and Column names provided in the SCHEMA. If asked for a specific attribute (e.g., "item name"), do NOT default to a generic "Name" column if a more specific column exists.
4. COLUMN ORDER: If asked to "list A and B", output exact order: SELECT A, B.
5. TOP-1 AGGREGATION (CRITICAL): For "top 1", "highest", or "largest" queries, use `ORDER BY col DESC LIMIT 1`. Do NOT use a subquery in the WHERE clause unless absolutely necessary. This allows you to include the sorted column in the SELECT clause (EVIDENCE IN SELECT).
6. PRE-COMPUTED COLUMNS vs. FUNCTIONS: Check if an "average", "total", or "sum" column already exists before wrapping other columns in AVG() or SUM().
7. MANDATORY ALIASING: Prefix EVERY column with its table alias (e.g., T1.id, T2.name).
8. FOREIGN KEYS: Examine the "Foreign Key" section to match correct IDs. Do not join on descriptive names.
9. SQLITE DATETIME WORKAROUNDS: Use `strftime('%Y', col)` for Year, `strftime('%m', col)` for Month, and `(CAST(strftime('%m', col) AS INTEGER) + 2) / 3` for Quarter. Never use YEAR() or MONTH().
10. PREFER CTE (WITH): For multi-level grouping or filtering aggregates, use a CTE for correctness.
11. STRICT LITERAL PRESERVATION (ANTI-VALUE BLEEDING): The FEW-SHOT EXAMPLES are for structural reference ONLY. You MUST extract the actual filter values (like years, dates, genre names, country names) EXPLICITLY from the CURRENT QUESTION. NEVER copy literal values (e.g., '2021', 'Rock', 'Jazz', '1') from the examples into your new SQL.
12. RANGE IMPLICIT ORDERING (ANTI-DATASET BIAS): For questions filtering by thresholds (greater than, longer than), implicitly add an ORDER BY clause sorting by that metric descending to present the most significant results first.

[STRICT OUTPUT FORMATTING RULES]
13. HUMAN-READABLE PROJECTION: If the query asks for a single entity property, select its descriptive text column (Name/Title). HOWEVER, if the query asks for a general list (e.g., "Tất cả nghệ sĩ", "Danh sách 10...", "Album cuối cùng"), you MUST SELECT BOTH the Primary Key ID and the descriptive Name (e.g., `SELECT MaNgheSi, Ten`).
14. PROJECTION COMPLETENESS: For 'all information', default to SELECT * instead of returning IDs.
15. EVIDENCE IN SELECT (CONDITIONAL): 
    - If filtering by a literal threshold (e.g., "dài hơn 5 phút", "giá cao nhất"), you MUST include the property column (Duration, Price) alongside the Name.
    - If using GROUP BY and ORDER BY COUNT()/SUM() to find "the most" or "the least" of an entity (e.g., "Nghệ sĩ ít album nhất"), DO NOT include the aggregated COUNT/SUM in the SELECT clause unless explicitly asked. Select only the entity Name.
16. QUANTITATIVE VS QUALITATIVE: If the question explicitly asks for "số lượng", "bao nhiêu", "tổng số", "count", or "how many", YOU MUST USE AGGREGATION like COUNT() or SUM(). NEVER return a list of names/titles.
17. MANDATORY DISTINCT: When querying parent entities based on child entities, ALWAYS use SELECT DISTINCT.
"""


def _build_schema_text_for_prompt(schema_context: str) -> str:
    """
    Build schema text for system prompt.
    If schema_context is already text (from schema indexer) -> use directly.
    """
    if schema_context and schema_context.strip():
        return schema_context.strip()
    return (
        "No schema context available. "
        "Please infer the schema from the question and generate a safe query."
    )


def sql_generator_node(state: AgentState) -> AgentState:
    """Node: SQLGenerator with retry and fallback."""
    state.generation_attempts += 1
    few_shot_text = ""
    # 1. Retrieve Few-shot examples
    if not state.evidence:
        try:
            from pathlib import Path
            from src.rag.few_shot_retriever import FewShotRetriever
            dataset_type = Path(state.current_db_path).stem if state.current_db_path else None
            
            retriever = FewShotRetriever()
            # Lọc few-shot example theo dataset_type (db_name) để tránh lấy nhầm ví dụ của DB khác
            examples = retriever.retrieve(state.user_question, k=3, dataset_type=dataset_type)
            if examples:
                few_shot_text = "BELOW ARE SIMILAR FEW-SHOT EXAMPLES:\n"
                for i, ex in enumerate(examples):
                    few_shot_text += f"Example {i+1}:\n- Question: {ex['question']}\n- SQL: {ex['sql']}\n\n"
        except Exception as e:
            log.warning("few_shot_failed", error=str(e))

    # 2. Build schema and system prompt
    schema_text = _build_schema_text_for_prompt(state.schema_context)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text) + SQL_GENERATOR_RULES

    # 3. Combine Plan and Few-shot examples into User Prompt
    plan_ctx = ""
    if state.plan:
        steps = "\n".join(f"Step {s['step_id']}: {s['description']}" for s in state.plan.get("steps", []))
        plan_ctx = f"EXECUTION PLAN:\n{steps}\n"

    # Process BIRD evidence
    evidence_ctx = f"BUSINESS RULES / EVIDENCE:\n{state.evidence}\n" if state.evidence else ""

    error_ctx = ""
    if state.execution_error:
        error_ctx = f"\nPREVIOUS SQL FAILED:\nSQL: {state.generated_sql}\nERROR: {state.execution_error}\nPlease FIX the SQL based on this error.\n"

    user_prompt = f"""{few_shot_text}
CURRENT QUESTION: {state.user_question}
{evidence_ctx}
{plan_ctx}
{error_ctx}
Rely on the SCHEMA, BUSINESS RULES (if any), and the structure of the few-shot examples to write the most accurate SQL. Return ONLY JSON."""

    raw = ""
    sql = ""
    import sqlite3
    import time
    
    for attempt in range(3):
        if attempt > 0:
            log.info("sql_gen_waiting", seconds=2, reason="Rate limit protection")
            time.sleep(2)
        try:
            raw = invoke(
                prompt=user_prompt,
                model=config.LLM_MODEL_PRO,
                temperature=0.1,  # Tăng chút xíu để nó đa dạng hóa cách sửa lỗi
                max_tokens=1536,
                system_prompt=system_prompt,
            )
            
            sql = _extract_sql(raw)
            is_safe, issues = _validate_dangerous(sql)
            
            if not is_safe or not sql:
                error_msg = f"Security check failed or no SQL extracted: {issues}"
                log.warning("sql_extract_failed", raw=raw[:200], sql=sql, issues=issues)
                user_prompt += f"\n\nATTEMPT {attempt+1} FAILED:\nSQL generated: {sql}\nError: {error_msg}\nPlease FIX this."
                continue
                
            # Self-correction: Validate SQL directly against the database
            if state.current_db_path:
                try:
                    conn = sqlite3.connect(state.current_db_path)
                    conn.execute(f"EXPLAIN QUERY PLAN {sql}")
                    conn.close()
                    log.info("sql_validation_success", sql=sql)
                    break # Syntax is fully valid and tables/columns exist!
                except sqlite3.Error as e:
                    conn.close()
                    error_msg = str(e)
                    print(f"\\n[Self-Correction Triggered] Attempt: {attempt+1}\\nError: {error_msg}\\nSQL: {sql}\\n")
                    log.warning("sql_self_correction_triggered", attempt=attempt+1, error=error_msg, sql=sql)
                    user_prompt += f"\n\nATTEMPT {attempt+1} FAILED with SQLite error:\nSQL generated: {sql}\nError: {error_msg}\nDO NOT use columns or tables that do not exist in the schema. Please fix the SQL logic."
                    continue
            else:
                break # No DB path to validate against, assume it's OK
                
        except Exception as e:
            log.warning("sql_gen_retry", attempt=attempt + 1, error=str(e))
            if attempt == 2:
                raw = ""

    # Re-extract one last time in case the loop ended
    sql = _extract_sql(raw)
    is_safe, _ = _validate_dangerous(sql)

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
