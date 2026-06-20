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


def _validate_sql_safety(sql: str) -> tuple[bool, list]:
    """Backward-compatible public name used by tests and external callers."""
    return _validate_dangerous(sql)


# ── System prompt (dynamic — schema injected via user prompt) ───────────────
SYSTEM_PROMPT_TEMPLATE = """# 1. Task Context
You are an expert {db_dialect} query generator. Your task is to generate precise SQL queries from natural language.

# 2. Tone Context
Be meticulous and strict. Write safe and correct SQL. You must adhere strictly to the schema provided.

# 3. Background Data
{schema}

# 4. Detailed Task Description & Rules
CRITICAL RULES FOR SQL GENERATION:
1. ONLY SELECT — never DROP/INSERT/UPDATE/DELETE
2. STRICT WHITELISTING: DO NOT hallucinate table or column names. You MUST ONLY use the EXACT tables and columns defined in the schema above.
3. Define your own table aliases — do NOT use pre-defined aliases. Always use explicit column names from the schema above.
4. Escape single quotes: '' not \\'  (e.g. WHERE Name = '90''s Music')
5. LIMIT must be integer: LIMIT 10 (not LIMIT 1st). Always end SQL with semicolon.
6. EVIDENCE IS KING: If "BUSINESS RULES / EVIDENCE" are provided, strictly follow them. Do NOT use your own assumptions.
7. COLUMN ORDER: If asked to "list A and B", output exact order: SELECT A, B.
8. TOP-1 AGGREGATION (CRITICAL): For "top 1", "highest", or "largest" queries, use `ORDER BY col DESC LIMIT 1`. DO NOT use subquery in WHERE clause for this.
9. PRE-COMPUTED COLUMNS vs. FUNCTIONS: Check if an "average", "total", or "sum" column already exists before wrapping other columns in AVG() or SUM().
10. MANDATORY ALIASING: Prefix EVERY column with its table alias (e.g., T1.id, T2.name).
11. FOREIGN KEYS: Examine the "Foreign Key" section to match correct IDs. Do not join on descriptive names.
12. DIALECT DATETIME WORKAROUNDS: Be highly aware of {db_dialect} syntax. For SQLite: `strftime('%Y', col)`. For Postgres: `DATE_TRUNC('year', col)` or `EXTRACT(YEAR FROM col)`. For MySQL: `YEAR(col)`.
13. PREFER CTE (WITH): For multi-level grouping or filtering aggregates, use a CTE for correctness.
14. STRICT LITERAL PRESERVATION (ANTI-VALUE BLEEDING): The FEW-SHOT EXAMPLES are for structural reference ONLY. You MUST extract the actual filter values EXPLICITLY from the CURRENT QUESTION.
15. RANGE IMPLICIT ORDERING: For questions filtering by thresholds (greater than, longer than), implicitly add an ORDER BY clause sorting by that metric descending.
16. EXACT PROJECTION: SELECT only the columns or aggregates explicitly requested by the user, in the requested order. Do not add IDs, names, evidence columns, or metadata unless explicitly requested.
17. QUANTITATIVE VS QUALITATIVE: If asked for "số lượng", "bao nhiêu", "count", YOU MUST USE COUNT() or SUM(). NEVER return a list.
18. MANDATORY DISTINCT: When querying parent entities based on child entities, ALWAYS use SELECT DISTINCT.
19. LIMIT SEMANTICS: Use LIMIT only when the user explicitly requests a row count/top-N result, or when LIMIT is required to express a superlative such as top 1. Never add a safety/display LIMIT to the SQL; result pagination belongs to the UI or execution layer.

# 5. Examples
(Examples will be provided in the user prompt if retrieved from memory)

# 8. Thinking step by step
Think step by step before writing SQL. Map entities in the question to tables and columns in the schema. Check if aggregations or CTEs are needed. Ensure the generated SQL complies with all rules.

# 9. Output formatting
OUTPUT FORMAT (JSON):
{{"sql":"SELECT ...;","confidence":0.9,"reasoning":"brief explanation"}}
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
    if not state.evidence and state.evaluation_options.get("few_shot_enabled", True):
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
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text, db_dialect=state.db_dialect)

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

    # Format History
    history_str = "(No previous history provided)"
    if state.messages and len(state.messages) > 1:
        recent_msgs = state.messages[-5:-1]
        history_lines = []
        for m in recent_msgs:
            role = "User" if m.type == "human" else "AI"
            history_lines.append(f"{role}: {m.content}")
        history_str = "\n".join(history_lines)

    user_prompt = f"""# 6. Conversation History
{history_str}

# 7. Immediate Task
{few_shot_text}
{evidence_ctx}
{plan_ctx}
{error_ctx}
CURRENT QUESTION: {state.user_question}

Rely on the SCHEMA, BUSINESS RULES (if any), and the structure of the few-shot examples to write the most accurate SQL. Return ONLY JSON.

# 10. Prefilled response (if any)
(None)"""

    raw = ""
    sql = ""
    import sqlite3
    import time
    
    for attempt in range(2):
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
                telemetry_label="sql_generator",
            )
            
            sql = _extract_sql(raw)
            is_safe, issues = _validate_dangerous(sql)
            
            if not is_safe or not sql:
                error_msg = f"Security check failed or no SQL extracted: {issues}"
                log.warning("sql_extract_failed", raw=raw[:200], sql=sql, issues=issues)
                user_prompt += f"\n\nATTEMPT {attempt+1} FAILED:\nSQL generated: {sql}\nError: {error_msg}\nPlease FIX this."
                continue
                
            # Self-correction: Validate SQL directly against the database
            # Chỉ chạy EXPLAIN cho SQLite (local, 0ms latency)
            # Cloud DB (Postgres/MySQL) sẽ được Validator kiểm tra bảng/cột sau — tránh ping mạng thừa
            if (
                state.evaluation_options.get("self_correction_enabled", True)
                and state.current_db_path
                and state.db_dialect == "sqlite"
            ):
                from src.db import get_db_manager
                from sqlalchemy import text
                try:
                    db = get_db_manager(state.current_db_path)
                    with db.engine.connect() as conn:
                        conn.execute(text(f"EXPLAIN QUERY PLAN {sql}"))
                    log.info("sql_validation_success", sql=sql)
                    break # Syntax is fully valid and tables/columns exist!
                except Exception as e:
                    error_msg = str(e).strip()
                    print(f"\\n[Self-Correction Triggered] Attempt: {attempt+1}\\nError: {error_msg}\\nSQL: {sql}\\n")
                    log.warning("sql_self_correction_triggered", attempt=attempt+1, error=error_msg, sql=sql)
                    user_prompt += f"\n\nATTEMPT {attempt+1} FAILED with Database error:\nSQL generated: {sql}\nError: {error_msg}\nDO NOT use columns or tables that do not exist in the schema. Please fix the SQL logic."
                    continue
            else:
                break # Cloud DB hoặc không có DB → tin tưởng LLM, để Validator kiểm tra sau
                
        except Exception as e:
            log.warning("sql_gen_retry", attempt=attempt + 1, error=str(e))
            if attempt == 1:
                raw = ""

    # Re-extract one last time in case the loop ended
    sql = _extract_sql(raw)
    is_safe, _ = _validate_dangerous(sql)

    if not is_safe or not sql:
        state.generated_sql = ""
        state.sql_confidence = 0.0
        state.error = "SQL generation failed after all attempts"
        state.execution_error = state.error
        state.next_agent = "error"
        state.current_step = "sql_generation_failed"
        log.warning("sql_generation_failed", attempts=state.generation_attempts)
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
