"""
SQL Validator Agent — kiểm tra SQL trước khi execute.
Strategy: Dynamic table/column validation chỉ dùng khi CÓ DB connection.
Nếu validation fail → retry sql_generator với error context.
Execution layer vẫn catch runtime errors từ SQLite.
"""
import re
import structlog
from src.agents.state import AgentState
from src.db import get_db_manager

log = structlog.get_logger("validator")

DANGEROUS_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE",
    "ALTER", "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
]

# Những pattern chỉ ra LLM đang nhầm lẫn column (để gợi ý retry)
HINT_PATTERNS = [
    # Track.Title — nhầm Track.Name
    (r'\btrack\s*\.\s*title\b', "Track.Name (not Title)"),
    # Artist.GenreId — nhầm
    (r'\bartist\s*\.\s*genreid\b', "Artist has no GenreId"),
    # Invoice.AlbumId — nhầm
    (r'\binvoice\s*\.\s*albumid\b', "Invoice has no AlbumId"),
    # Employee.CustomerId — nhầm
    (r'\bemployee\s*\.\s*customerid\b', "Employee has no CustomerId"),
]


def _fix_common_errors(sql: str) -> str:
    """Tự động sửa lỗi syntax phổ biến."""
    sql = re.sub(r'\bSELECT\s+TOP\s+(\d+)\b', 'SELECT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bLIMIT\s+(\d+)(st|nd|rd|th)\b', r'LIMIT \1', sql, flags=re.IGNORECASE)
    sql = re.sub(r"\\'", "''", sql)
    return sql


def _get_db(db_path: str = ""):
    if db_path:
        return get_db_manager(db_path)
    return get_db_manager()


def _extract_table_names(sql: str) -> list[str]:
    """Extract table names from FROM/JOIN (case-insensitive, unique)."""
    tables = set()
    
    # Identify CTE aliases (e.g., WITH CTE_NAME AS ( ... ) or , CTE_NAME AS ( ... ))
    cte_aliases = set()
    for m in re.finditer(r'(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', sql, re.IGNORECASE):
        cte_aliases.add(m.group(1).lower())
        
    # Hỗ trợ cả bảng không ngoặc và bảng có ngoặc kép/backticks/brackets (ví dụ: "Order Details")
    for m in re.finditer(r'\b(?:FROM|JOIN)\s+(?:`([^`]+)`|"([^"]+)"|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))', sql, re.IGNORECASE):
        t = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if t and t.upper() not in ("SELECT", "WITH"):
            t_lower = t.lower()
            if t_lower not in cte_aliases:
                tables.add(t_lower)
    return list(tables)


def _validate_tables(sql: str, db) -> list[str]:
    """Verify all tables exist in DB."""
    issues = []
    tables = _extract_table_names(sql)
    import structlog
    log = structlog.get_logger("validator")
    log.info("validate_tables_start", tables=tables, db_path=db.db_path)
    for t in tables:
        if not db.table_exists(t):
            issues.append(f"Table '{t}' does not exist in database")
    return issues


def _get_actual_table_name(db, table_lower: str) -> str:
    """Resolve lowercase table name to actual case-preserved name in DB."""
    import sqlite3
    with db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)=LOWER(?)",
            (table_lower,)
        )
        row = cur.fetchone()
        return row[0] if row else table_lower


def _validate_column_refs(sql: str, db) -> list[str]:
    """
    Verify all table.column references exist.
    Table names are case-insensitive (resolved via LOWER lookup).
    Column names are case-sensitive (matched against actual DB column names).
    """
    issues = []

    # Build alias map: alias → actual_table_name
    alias_map: dict[str, str] = {}
    for m in re.finditer(
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b',
        sql, re.IGNORECASE
    ):
        raw_table = m.group(1)
        alias = m.group(2).lower()
        if db.table_exists(raw_table):
            alias_map[alias] = _get_actual_table_name(db, raw_table.lower())

    # Extract all table.column references
    for m in re.finditer(
        r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b', sql
    ):
        table_part = m.group(1).lower()
        col_part = m.group(2)
        actual_table = alias_map.get(table_part, table_part)

        if not db.table_exists(actual_table):
            continue  # table check will catch this separately

        cols = db.get_table_columns(actual_table)
        if col_part not in cols:
            issues.append(
                f"Column '{actual_table}.{col_part}' not found. "
                f"Available: {', '.join(cols[:10])}"
            )

    # Check BARE columns (no table prefix) in SELECT clause
    tables = _extract_table_names(sql)
    if tables:
        # Build lowercase col → (tables, actual_column_name) mapping
        # IMPORTANT: use actual case-preserved table names from DB
        col_to_tables: dict[str, list[tuple[str, str]]] = {}
        for tbl_lower in tables:
            actual_tbl = _get_actual_table_name(db, tbl_lower)
            if db.table_exists(actual_tbl):
                for col in db.get_table_columns(actual_tbl):
                    key = col.lower()
                    if key not in col_to_tables:
                        col_to_tables[key] = []
                    col_to_tables[key].append((actual_tbl, col))

        # Extract bare columns from SELECT (everything between SELECT and FROM)
        m_select = re.search(r'\bSELECT\s+(.*?)\s+FROM\b', sql, re.IGNORECASE | re.DOTALL)
        if m_select:
            select_cols = re.split(r',\s*', m_select.group(1))
            for col_part in select_cols:
                col_stripped = col_part.strip()
                # Strip DISTINCT prefix (e.g. "DISTINCT Country" → "Country")
                col_stripped = re.sub(r'^\s*DISTINCT\s+', '', col_stripped, flags=re.IGNORECASE).strip()
                col_lower = col_stripped.lower()
                # Skip: has dot (T1.Name), has function, has alias, is *
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col_stripped):
                    continue
                if '.' in col_part or col_part.strip() == '*':
                    continue
                if re.search(r'\b(COUNT|SUM|AVG|MAX|MIN|ROUND|STRFTIME|LENGTH)\s*\(', col_part, re.IGNORECASE):
                    continue
                if re.search(r'\bAS\s+\w+$', col_part.strip(), re.IGNORECASE):
                    continue

                # Bare column — check if it exists in >1 table (ambiguous) or 0 tables (bad)
                matches = col_to_tables.get(col_lower, [])
                table_names = [t for t, _ in matches]
                if len(matches) == 0:
                    issues.append(
                        f"Column '{col_stripped}' not found in any table. "
                        f"Available: {list(col_to_tables.keys())[:10]}"
                    )
                elif len(matches) > 1:
                    issues.append(
                        f"Column '{col_stripped}' in SELECT is ambiguous (exists in: {', '.join(table_names)}). "
                        f"Use table alias: {table_names[0]}.{col_stripped}"
                    )

    return issues


def _check_hint_patterns(sql: str) -> list[str]:
    """Detect common LLM mistakes via regex patterns."""
    issues = []
    for pattern, hint in HINT_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            issues.append(hint)
    return issues


def hard_validate(sql: str, db=None) -> dict:
    """
    Dynamic SQL validation.
    - Syntax checks: always run
    - Table/column checks: only when db is provided
    """
    if not sql or not sql.strip():
        return {"valid": False, "issues": ["SQL empty"], "risk_level": "high"}

    issues = []
    sql_upper = sql.strip().upper()

    # 1. SELECT/WITH required
    first_word = sql_upper.split()[0] if sql_upper.split() else ""
    if first_word not in ("SELECT", "WITH"):
        return {
            "valid": False,
            "issues": [f"Must start with SELECT, got: '{sql.strip()[:30]}'"],
            "risk_level": "high"
        }

    # 2. Dangerous keywords
    for kw in DANGEROUS_KEYWORDS:
        if re.search(r'\b' + kw + r'\b', sql_upper):
            issues.append(f"Dangerous keyword: {kw}")
    if issues:
        return {"valid": False, "issues": issues, "risk_level": "high"}

    # 3. Hint patterns (detect LLM mistakes even without DB)
    hint_issues = _check_hint_patterns(sql)
    if hint_issues:
        issues.extend(hint_issues)

    # 4. Table/column validation (dynamic, requires DB)
    if db is not None:
        table_issues = _validate_tables(sql, db)
        issues.extend(table_issues)

        if not table_issues and db is not None:  # only check columns if tables are valid and DB available
            col_issues = _validate_column_refs(sql, db)
            issues.extend(col_issues)

    if issues:
        return {"valid": False, "issues": issues, "risk_level": "medium"}
    return {"valid": True, "issues": [], "risk_level": "low"}


def validate_sql(sql: str, db_path: str = "") -> dict:
    """Public API — validate SQL."""
    fixed = _fix_common_errors(sql)
    if fixed != sql:
        log.info("validator_fix", original=sql[:80], fixed=fixed[:80])
    sql = fixed

    db = _get_db(db_path) if db_path else None
    result = hard_validate(sql, db)

    if result["valid"]:
        log.info("validator_pass", sql=sql[:80])
    else:
        log.info("validator_fail", sql=sql[:80], issues=result["issues"])
    return result


def validator_node(state: AgentState) -> AgentState:
    sql = state.generated_sql
    if not sql:
        state.error = "No SQL to validate"
        state.next_agent = "error"
        return state

    db_path = state.current_db_path or ""
    # Spider case: no DB file → skip dynamic table/column validation
    db = _get_db(db_path) if db_path else None

    result = hard_validate(sql, db)

    # Missing Joins Check (Soft warning to avoid failing on plural/singular mismatch like pet vs pets)
    tables_in_sql = _extract_table_names(sql)
    planned_tables = state.tables_identified if hasattr(state, 'tables_identified') and state.tables_identified else []
    missing_tables = [pt for pt in planned_tables if pt.lower() not in tables_in_sql and pt.lower() != '']
    if missing_tables:
        result.setdefault("issues", []).append(f"Notice: The Query Planner mentioned {planned_tables}, but SQL used {tables_in_sql}. (Might be singular/plural difference)")
        # We DO NOT set result["valid"] = False here because of false positives!

    if result["valid"]:
        state.next_agent = "executor"
        state.current_step = "validated"
        log.info("validation_passed", sql=sql[:80])
    else:
        issues_str = "; ".join(result.get("issues", []))
        print(f"\\n[Validation Failed]\\nIssues: {issues_str}\\nSQL: {sql[:150]}\\n")
        log.warning("validation_failed", sql=sql[:80], issues=issues_str, retry=state.retry_count)

        if state.retry_count < state.max_retries:
            state.retry_count += 1
            state.next_agent = "sql_generator"
            state.current_step = "validation_failed_retry"
            state.schema_context = (
                (state.schema_context or "") +
                f"\n\nSQL ERROR: {sql}\nISSUE: {issues_str}\nFix the SQL above."
            )
        else:
            state.error = f"Validation failed after {state.retry_count} retries: {issues_str}"
            state.next_agent = "error"

    return state
