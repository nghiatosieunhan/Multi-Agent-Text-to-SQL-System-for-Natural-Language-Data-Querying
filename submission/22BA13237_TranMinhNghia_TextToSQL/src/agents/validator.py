"""
SQL Validator Agent — kiểm tra SQL trước khi execute.
Strategy: Dynamic table/column validation chỉ dùng khi CÓ DB connection.
Nếu validation fail → retry sql_generator với error context.
Execution layer vẫn catch runtime errors từ SQLite.
"""
import re
import structlog
import sqlglot
from src.agents.state import AgentState
from src.db import get_db_manager

log = structlog.get_logger("validator")

DANGEROUS_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE",
    "ALTER", "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
]

# Tắt các pattern hardcode của Chinook để tránh false positives cho các DB khác
HINT_PATTERNS = []
REPAIRABLE_SEMANTIC_CODES = {
    "PROJECTION_MISMATCH",
    "PROJECTION_TOO_WIDE",
    "MISSING_OUTPUT_COLUMN",
    "UNEXPECTED_OUTPUT_COLUMN",
    "SELECT_STAR_USED",
    "POSSIBLE_MISSING_LIMIT",
    "POSSIBLE_WRONG_LIMIT",
    "POSSIBLE_WRONG_DATE_COLUMN",
    "POSSIBLE_MISSING_DISTINCT",
    "POSSIBLE_MISSING_GROUP_BY",
    "ROUND_REQUIRED_MISSING",
    "METRIC_EXPRESSION_MISSING_TOKEN",
}

def _fix_common_errors(sql: str) -> str:
    """Tự động sửa lỗi syntax phổ biến."""
    top_match = re.search(r'\bSELECT\s+TOP\s+(\d+)\s+', sql, flags=re.IGNORECASE)

    if top_match:
        n = top_match.group(1)
        sql = re.sub(r'\bSELECT\s+TOP\s+\d+\s+', 'SELECT ', sql, flags=re.IGNORECASE)

        if not re.search(r'\bLIMIT\s+\d+\b', sql, flags=re.IGNORECASE):
            sql = sql.rstrip().rstrip(";") + f" LIMIT {n};"

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
    from sqlalchemy import inspect
    try:
        inspector = inspect(db.engine)
        for t in inspector.get_table_names():
            if t.lower() == table_lower.lower():
                return t
    except Exception:
        pass
    return table_lower


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


def _make_warning(code: str, message: str, expected: str = "", actual: str = "") -> dict:
    return {
        "code": code,
        "message": message,
        "severity": "warning",
        "expected": expected,
        "actual": actual,
    }

def _make_error(code: str, message: str, expected: str = "", actual: str = "") -> dict:
    return {
        "code": code,
        "message": message,
        "severity": "error",
        "expected": expected,
        "actual": actual,
    }

def _make_validation_report(
    result: dict,
    errors: list[dict] | None = None,
    warnings: list[dict] | None = None,
) -> dict:
    errors = errors or []
    warnings = warnings or []

    risk_level = result.get("risk_level", "medium")

    if errors:
        if risk_level == "high":
            risk_score = 0.9
        else:
            risk_score = 0.6
    elif warnings:
        risk_score = 0.4
    else:
        risk_score = 0.0

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "risk_score": risk_score,
        "repairable": risk_score < 0.9,
    }

def _validate_against_query_spec_soft(sql: str, query_spec: dict | None) -> list[dict]:
    warnings = []

    if not query_spec:
        return warnings

    sql_upper = sql.upper()

    # DISTINCT check
    dedup = query_spec.get("deduplication", "none")
    has_distinct = bool(re.search(r"\bSELECT\s+DISTINCT\b", sql, re.IGNORECASE))

    if dedup == "DISTINCT" and not has_distinct:
        warnings.append(
            _make_warning(
                code="POSSIBLE_MISSING_DISTINCT",
                message="QuerySpec requires DISTINCT but SQL does not use SELECT DISTINCT.",
                expected="SELECT DISTINCT",
                actual="No SELECT DISTINCT found",
            )
        )

    if dedup == "none" and has_distinct:
        warnings.append(
            _make_warning(
                code="POSSIBLE_UNEXPECTED_DISTINCT",
                message="SQL uses DISTINCT but QuerySpec.deduplication is none.",
                expected="No DISTINCT",
                actual="SELECT DISTINCT found",
            )
        )

    # LIMIT check
    expected_limit = query_spec.get("limit")
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)

    if expected_limit is not None:
        if not limit_match:
            warnings.append(
                _make_warning(
                    code="POSSIBLE_MISSING_LIMIT",
                    message=f"QuerySpec requires LIMIT {expected_limit}, but SQL has no LIMIT.",
                    expected=f"LIMIT {expected_limit}",
                    actual="No LIMIT found",
                )
            )
        elif int(limit_match.group(1)) != int(expected_limit):
            warnings.append(
                _make_warning(
                    code="POSSIBLE_WRONG_LIMIT",
                    message=f"QuerySpec requires LIMIT {expected_limit}, but SQL uses LIMIT {limit_match.group(1)}.",
                    expected=f"LIMIT {expected_limit}",
                    actual=f"LIMIT {limit_match.group(1)}",
                )
            )

    if expected_limit is None and limit_match:
        warnings.append(
            _make_warning(
                code="POSSIBLE_UNEXPECTED_LIMIT",
                message="SQL has LIMIT but QuerySpec does not request a limit.",
                expected="No LIMIT",
                actual=limit_match.group(0),
            )
        )

    # GROUP BY check
    group_by = query_spec.get("group_by", [])
    aggregations = query_spec.get("aggregations", [])

    if aggregations and group_by and "GROUP BY" not in sql_upper:
        warnings.append(
            _make_warning(
                code="POSSIBLE_MISSING_GROUP_BY",
                message="QuerySpec has aggregation and group_by, but SQL does not contain GROUP BY. This may still be valid if subquery/window function is used.",
                expected=f"GROUP BY {group_by}",
                actual="No GROUP BY keyword found",
            )
        )

    # Date semantics check
    for d in query_spec.get("date_semantics", []):
        expected_col = d.get("column", "")
        if not expected_col:
            continue

        expected_col_name = expected_col.split(".")[-1]

        if expected_col_name not in sql:
            warnings.append(
                _make_warning(
                    code="POSSIBLE_WRONG_DATE_COLUMN",
                    message=f"QuerySpec expects date column {expected_col}, but SQL may not use it.",
                    expected=expected_col,
                    actual="Expected date column not found by simple check",
                )
            )

    # Metric rules check
    for metric in query_spec.get("metric_rules", []):
        alias = metric.get("alias", "")
        expression = metric.get("expression", "")
        rounding = metric.get("rounding", None)
        required = metric.get("required", True)

        if not required:
            continue

        # If QuerySpec requires rounding for a metric, SQL should contain ROUND(...)
        if rounding is not None and not re.search(r"\bROUND\s*\(", sql, re.IGNORECASE):
            warnings.append(
                _make_warning(
                    code="ROUND_REQUIRED_MISSING",
                    message=f"Metric '{alias}' requires ROUND(..., {rounding}), but SQL does not use ROUND.",
                    expected=f"ROUND(..., {rounding}) for {alias}",
                    actual="No ROUND(...) found",
                )
            )

        # Lightweight formula-token check.
        # This is intentionally simple: only check important column/function tokens
        # mentioned in the metric expression.
        if expression:
            important_tokens = []
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression):
                token_lower = token.lower()
                if token_lower in {
                    "sum", "avg", "count", "min", "max", "round",
                    "as", "distinct", "case", "when", "then", "else", "end"
                }:
                    continue
                if token_lower not in {"order", "details"}:
                    important_tokens.append(token)

            sql_lower = sql.lower()
            missing_tokens = []
            for token in set(important_tokens):
                if token.lower() not in sql_lower:
                    missing_tokens.append(token)

            if missing_tokens:
                warnings.append(
                    _make_warning(
                        code="METRIC_EXPRESSION_MISSING_TOKEN",
                        message=f"SQL may not follow metric rule for '{alias}'. Missing formula tokens: {missing_tokens}",
                        expected=expression,
                        actual=sql[:160],
                    )
                )
    return warnings

def _norm_output_col(c: str) -> str:
    """
    Normalize final output column names for QuerySpec projection comparison.
    We compare exposed output names, not full internal expressions.
    """
    c = str(c or "").strip()
    c = c.strip('"`[]')
    c = re.sub(r".*\s+AS\s+", "", c, flags=re.IGNORECASE)
    if re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*", c):
        c = c.split(".")[-1]
    else:
        c = re.sub(r"\b[A-Za-z_][\w$]*\.", "", c)
        c = re.sub(r"\s+", " ", c)
    return c.lower()

def _validate_projection(sql: str, query_spec: dict) -> list[dict]:
    warnings = []
    if not query_spec:
        return warnings
    output_columns = query_spec.get("output_columns", [])
    if not output_columns:
        return warnings

    try:
        parsed = sqlglot.parse_one(sql, dialect="sqlite")
        if not parsed or not parsed.args.get("expressions"):
            return warnings
            
        select_expressions = parsed.args.get("expressions")
        actual_cols = []
        has_star = False
        
        for expr in select_expressions:
            if isinstance(expr, sqlglot.exp.Star):
                has_star = True
                actual_cols.append("*")
            elif isinstance(expr, sqlglot.exp.Alias):
                actual_cols.append(expr.alias_or_name.lower())
            elif isinstance(expr, sqlglot.exp.Column):
                actual_cols.append(expr.name.lower())
            else:
                actual_cols.append(expr.sql(dialect="sqlite").lower())
                
        expected_cols = [_norm_output_col(c) for c in output_columns]
        actual_cols_norm = [_norm_output_col(c) for c in actual_cols]
        
        explicit_star_expected = expected_cols == ["*"]
        if has_star and not explicit_star_expected:
            warnings.append(
                _make_warning(
                    code="SELECT_STAR_USED",
                    message="Query uses SELECT * instead of specifying explicit columns.",
                    expected=str(expected_cols),
                    actual="SELECT *"
                )
            )
            return warnings
            
        if actual_cols_norm != expected_cols:
            warnings.append(
                _make_warning(
                    code="PROJECTION_MISMATCH",
                    message="Final SELECT columns do not exactly match QuerySpec.output_columns in name/order.",
                    expected=str(expected_cols),
                    actual=str(actual_cols_norm),
                )
            )

        missing = [c for c in expected_cols if c not in actual_cols_norm]
        extra = [c for c in actual_cols_norm if c not in expected_cols]

        if missing:
            warnings.append(
                _make_warning(
                    code="MISSING_OUTPUT_COLUMN",
                    message="Final SELECT misses required QuerySpec output columns.",
                    expected=str(expected_cols),
                    actual=str(actual_cols_norm),
                )
            )

        if extra:
            warnings.append(
                _make_warning(
                    code="UNEXPECTED_OUTPUT_COLUMN",
                    message="Final SELECT contains columns not requested by QuerySpec.",
                    expected=str(expected_cols),
                    actual=str(actual_cols_norm),
                )
            )
    except Exception as e:
        log.warning("sqlglot_parse_error", error=str(e), sql=sql[:50])

    return warnings


def validator_node(state: AgentState) -> AgentState:
    sql = state.generated_sql
    if not sql:
        state.error = "No SQL to validate"
        state.next_agent = "error"
        return state

    if not state.evaluation_options.get("validator_enabled", True):
        state.sql_validation = {"valid": True, "issues": [], "bypassed": True}
        state.validation_report = {
            "valid": True,
            "errors": [],
            "risk_score": 0.0,
            "repairable": False,
        }
        state.current_step = "validation_bypassed"
        state.next_agent = "executor"
        return state

    fixed_sql = _fix_common_errors(sql)
    if fixed_sql != sql:
        log.info("validator_fix", original=sql[:80], fixed=fixed_sql[:80])
        state.generated_sql = fixed_sql
        sql = fixed_sql

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

    # Soft validation against QuerySpec (Warnings only)
    effective_query_spec = state.query_spec
    benchmark_cols = (state.benchmark_context or {}).get("output_columns") or []
    benchmark_limit = (state.benchmark_context or {}).get("limit")
    if benchmark_cols and not effective_query_spec:
        effective_query_spec = {"output_columns": benchmark_cols}
    if benchmark_limit is not None:
        if not effective_query_spec:
            effective_query_spec = {}
        if effective_query_spec.get("limit") is None:
            effective_query_spec["limit"] = benchmark_limit

    if state.evaluation_options.get("semantic_validation_enabled", True):
        semantic_warnings = _validate_against_query_spec_soft(sql, effective_query_spec)
        
        if state.evaluation_options.get("projection_validation_enabled", True):
            projection_warnings = _validate_projection(sql, effective_query_spec)
            semantic_warnings.extend(projection_warnings)
    else:
        semantic_warnings = []

    if result["valid"]:
        if semantic_warnings:
            log.warning("semantic_validation_warning", warnings=semantic_warnings, sql=sql[:80])

            repairable_semantic = [
                w for w in semantic_warnings
                if isinstance(w, dict) and w.get("code") in REPAIRABLE_SEMANTIC_CODES
            ]

            non_repairable_warnings = [
                w for w in semantic_warnings
                if w not in repairable_semantic
            ]

            if repairable_semantic and state.retry_count < state.max_retries:
                state.validation_report = _make_validation_report(
                    result,
                    errors=repairable_semantic,
                    warnings=non_repairable_warnings,
                )

                state.retry_count += 1
                state.next_agent = "sql_generator"
                state.current_step = "semantic_validation_retry"

                log.warning(
                    "semantic_validation_retry",
                    errors=repairable_semantic,
                    retry=state.retry_count,
                    sql=sql[:120],
                )
            else:
                state.validation_report = _make_validation_report(
                    result,
                    errors=[],
                    warnings=semantic_warnings,
                )

                state.next_agent = "executor"
                state.current_step = "validated_with_warnings"
        else:
            state.validation_report = _make_validation_report(
                result,
                errors=[],
                warnings=[],
            )

            log.info("validation_passed", sql=sql[:80])
            state.next_agent = "executor"
            state.current_step = "validated"
    else:
        schema_errors = [
            _make_error(code="SCHEMA_OR_SAFETY_ERROR", message=issue)
            for issue in result.get("issues", [])
        ]
        
        state.validation_report = _make_validation_report(
            result,
            errors=schema_errors,
            warnings=semantic_warnings,
        )
        
        issues_str = "; ".join(result.get("issues", []))
        print(f"\\n[Validation Failed]\\nIssues: {issues_str}\\nSQL: {sql[:150]}\\n")
        log.warning("validation_failed", sql=sql[:80], issues=issues_str, retry=state.retry_count)

        if (
            state.validation_report.get("repairable", True)
            and state.retry_count < state.max_retries
        ):
            state.retry_count += 1
            state.next_agent = "sql_generator"
            state.current_step = "validation_failed_retry"
        else:
            state.error = f"Validation failed after {state.retry_count} retries: {issues_str}"
            state.next_agent = "error"

    return state
