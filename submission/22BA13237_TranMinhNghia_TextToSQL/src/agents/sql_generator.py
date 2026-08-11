"""
SQL Generator Agent — generates SQL queries from natural language questions.
Improvements:
  - Uses QuerySpec (if available) as a strict contract for projection/grain/joins.
  - Uses structured validation_report for targeted repair instead of blind retry.
  - Exponential backoff on API rate limits.
"""
import json
import re
import time
import structlog
from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config

log = structlog.get_logger("sql_generator")


def _extract_sql(text: str) -> str:
    """Extract SQL from JSON or Markdown response."""
    sql = ""
    cleaned_text = re.sub(r'^```(?:json|sql)?\s*', '', text.strip(), flags=re.MULTILINE)
    cleaned_text = re.sub(r'\s*```$', '', cleaned_text, flags=re.MULTILINE)
    cleaned_text = cleaned_text.strip()

    json_ready_text = cleaned_text.replace('\n', ' ')

    def _sql_from_json_obj(obj) -> str:
        if not isinstance(obj, dict):
            return ""
        for key in ("sql", "SQL", "query", "Query", "sql_query", "generated_sql"):
            value = obj.get(key)
            if isinstance(value, str):
                candidate = value.strip()
                if candidate.upper().startswith(("SELECT", "WITH")):
                    return candidate
        for key in ("data", "result", "response"):
            nested = obj.get(key)
            candidate = _sql_from_json_obj(nested)
            if candidate:
                return candidate
        return ""

    try:
        obj = json.loads(json_ready_text)
        sql = _sql_from_json_obj(obj)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r'\{.*\}', json_ready_text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                sql = _sql_from_json_obj(obj)
            except (json.JSONDecodeError, TypeError):
                pass

    if not sql:
        patterns = [
            r"```sql\s*(.*?)\s*```",
            r"```\s*((?:SELECT|WITH).*?;)\s*```",
            r"((?:SELECT|WITH)\s+.*?;)",
            r"((?:SELECT|WITH)\s+[\s\S]+)$",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            if matches:
                sql = matches[0].strip()
                break

    if not sql and (
        cleaned_text.strip().upper().startswith("SELECT")
        or cleaned_text.strip().upper().startswith("WITH")
    ):
        sql = cleaned_text.strip()

    if sql:
        sql = sql.replace('\\"', '"')
        sql = sql.strip().rstrip("`").rstrip()
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


def _norm_output_name(name: str) -> str:
    """
    Normalize output column names for QuerySpec contract checking.
    This is intentionally simple: compare final exposed names, not full SQL expressions.
    """
    name = str(name or "").strip()
    name = name.strip('"`[]')
    name = re.sub(r".*\s+AS\s+", "", name, flags=re.IGNORECASE)
    if re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*", name):
        name = name.split(".")[-1]
    else:
        name = re.sub(r"\b[A-Za-z_][\w$]*\.", "", name)
        name = re.sub(r"\s+", " ", name)
    return name.lower()


def _quick_check_query_spec_contract(sql: str, query_spec: dict | None) -> list[str]:
    """
    Lightweight post-generation check.
    It verifies that the final OUTER SELECT matches QuerySpec.output_columns
    before accepting SQL as generated.

    This does not replace validator.py. It only prevents obviously wrong SQL
    from passing the generator self-correction step just because EXPLAIN succeeds.
    """
    issues = []

    if not query_spec:
        return issues

    expected_cols = query_spec.get("output_columns") or []
    if not expected_cols:
        return issues

    try:
        import sqlglot

        parsed = sqlglot.parse_one(sql, dialect="sqlite")
        if not parsed:
            return ["Could not parse SQL for QuerySpec contract check."]

        select_expressions = parsed.args.get("expressions") or []

        actual_cols = []
        has_star = False

        for expr in select_expressions:
            if isinstance(expr, sqlglot.exp.Star):
                has_star = True
                actual_cols.append("*")
            elif isinstance(expr, sqlglot.exp.Alias):
                actual_cols.append(expr.alias_or_name)
            elif isinstance(expr, sqlglot.exp.Column):
                actual_cols.append(expr.name)
            else:
                # For expressions without alias, keep expression text.
                # Example: FirstName || ' ' || LastName
                actual_cols.append(expr.sql(dialect="sqlite"))

        expected_norm = [_norm_output_name(c) for c in expected_cols]
        actual_norm = [_norm_output_name(c) for c in actual_cols]

        explicit_star_expected = expected_norm == ["*"]
        if has_star and not explicit_star_expected:
            issues.append(
                f"SELECT * is forbidden. expected={expected_cols}, actual={actual_cols}"
            )

        if actual_norm != expected_norm:
            issues.append(
                "Final SELECT does not match QuerySpec.output_columns. "
                f"expected={expected_cols}, actual={actual_cols}"
            )

    except Exception as e:
        issues.append(f"QuerySpec contract check failed: {e}")

    return issues

# ── System prompt ─────────────────────────────────────────────────────────────
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
16. EXACT PROJECTION: SELECT only the columns or aggregates explicitly requested. Use SELECT * only when QuerySpec.output_columns is exactly ["*"] or the user explicitly requests every field/full record. Otherwise never select all columns from an entity table. Do NOT include BLOB/image/long text columns such as Photo, Notes, PhotoPath unless explicitly requested.
17. QUANTITATIVE VS QUALITATIVE: If asked for "số lượng", "bao nhiêu", "count", YOU MUST USE COUNT() or SUM(). NEVER return a list.
18. MANDATORY DISTINCT: When querying parent entities based on child entities, ALWAYS use SELECT DISTINCT.
19. LIMIT SEMANTICS: Use LIMIT only when the user explicitly requests a row count/top-N result, or when LIMIT is required to express a superlative such as top 1. Never add a safety/display LIMIT to the SQL; result pagination belongs to the UI or execution layer.
20. QUERY SPEC IS LAW: If a QUERY SPECIFICATION is provided, you MUST STRICTLY FOLLOW it. The FINAL OUTER SELECT MUST MATCH exactly the columns listed in `Output columns`.
21. PROJECTION CONTRACT: Do NOT add extra descriptive columns, IDs, or metadata unless they are listed in `Output columns`. If QuerySpec is available, QuerySpec.output_columns overrides any generic SQL generation habit.
22. QUERY SPEC OVERRIDES EVERYTHING:
    If QUERY SPECIFICATION is provided, it overrides the current question wording, few-shot examples, planner output, and your own assumptions.

23. FINAL SELECT CONTRACT:
    The FINAL OUTER SELECT must return exactly the columns listed in QuerySpec.output_columns, in the same order.
    - Do not add extra columns.
    - Do not omit required columns.
    - Do not merge two output columns into one expression unless QuerySpec.output_columns explicitly contains a single merged column.
    - Do not split one requested output column into multiple columns.
    - Helper columns may appear inside CTEs/subqueries, but not in the final outer SELECT.

24. FEW-SHOT IS STRUCTURE ONLY:
    Few-shot examples are only for SQL pattern reference.
    Never copy their projection, aliases, constants, filters, LIMIT, ORDER BY, or column formatting unless they match the current QuerySpec.

25. ALIAS CONTRACT:
    If QuerySpec.output_columns contains aliases such as Revenue, TotalSpent, NumOrders, the final SELECT must expose those names using AS.
26. BENCHMARK CONTRACT:
    If a BENCHMARK CONTRACT section provides Final columns, the final SELECT must expose exactly those columns/aliases in that order.
    This contract is stronger than generic "minimal projection" guidance.
    If it provides Required LIMIT, include that LIMIT exactly.
    If it says ORDER BY is required, do not omit ORDER BY; infer the stable ordering from the question and use deterministic tie-breakers when needed.
    Do not default to ORDER BY ID alone unless the question explicitly asks for ID order.
    For "list N ... with company/product/supplier/employee/category name" queries without a recency/value sort, order by the descriptive name column first, then date/id tie-breakers.
    For threshold filters such as Freight > 80, Quantity >= 100, Discount > 0, or "highest/top", order by that metric DESC, then a stable name/id tie-breaker.
    For "recent/latest/gần nhất", order by the relevant date DESC, then ID DESC.
27. DOMAIN PROFILE HINTS:
    Follow domain-specific benchmark hints only when the BENCHMARK CONTRACT provides them.
    For example, if Numeric rounding is requested, wrap the relevant aggregate expression with ROUND(..., N).
    Do not apply Northwind-specific formulas or tie-breakers to other datasets unless they appear in the contract or schema/business evidence.
28. QUANTIFIED COMPARISONS:
    "greater/larger than ANY value in group B" means greater than MIN(B); "greater than ALL" means greater than MAX(B).
    "less/smaller than ANY" means less than MAX(B); "less than ALL" means less than MIN(B).
29. BOTH VALUES FOR ONE ENTITY:
    When one entity must have child rows matching both distinct values of the same column, do not write column = A AND column = B on one row.
    Use INTERSECT between the two entity queries, or GROUP BY the entity with HAVING COUNT(DISTINCT matching_column) = 2.
30. PRESERVE NATIVE COMPARISON SEMANTICS:
    Do not CAST a comparison, MIN/MAX, or ORDER BY column merely because sample values look numeric. Use the schema's native type and ordering unless the question or evidence explicitly requests conversion.
31. ANTI-MEMBERSHIP BY KEY:
    For "entities that do not have X", exclude entities by their stable primary/foreign key using NOT EXISTS or key NOT IN. Do not EXCEPT projected display attributes because different entities may share those values.
32. JOIN DEFAULT:
    Use INNER JOIN by default. Use LEFT JOIN only when the question explicitly asks to retain entities with no matching child, missing relationships, or zero counts.

# 5. Examples
(Examples will be provided in the user prompt if retrieved from memory)

# 8. Thinking step by step
Think step by step before writing SQL. Map entities in the question to tables and columns in the schema. Check if aggregations or CTEs are needed. Ensure the generated SQL complies with all rules.

# 9. Output formatting
OUTPUT FORMAT (JSON):
{{"sql":"SELECT ...;","confidence":0.9,"reasoning":"brief explanation"}}
"""


def _build_schema_text_for_prompt(schema_context: str) -> str:
    if schema_context and schema_context.strip():
        return schema_context.strip()
    return (
        "No schema context available. "
        "Please infer the schema from the question and generate a safe query."
    )


def _build_spec_context(query_spec: dict | None) -> str:
    """Render QuerySpec as a compact prompt section."""
    if not query_spec:
        return ""
    qs = query_spec
    lines = [
        "QUERY SPECIFICATION (follow exactly — this is a binding contract):",
        f"  Intent         : {qs.get('intent', '')}",
        f"  Output columns : {', '.join(qs.get('output_columns', []))}",
        f"  Proj. policy   : {qs.get('projection_policy', 'exact')}",
        f"  Grain          : {qs.get('output_grain', '')}",
        f"  Source tables  : {', '.join(qs.get('source_tables', []))}",
        f"  Deduplication  : {qs.get('deduplication', 'none')}",
    ]
    if qs.get('filters'):
        lines.append(f"  Filters        : {qs['filters']}")
    if qs.get('aggregations'):
        lines.append(f"  Aggregations   : {qs['aggregations']}")
    if qs.get("metric_rules"):
        lines.append("  Metric rules   :")
        for r in qs["metric_rules"]:
            lines.append(
                f"    - name={r.get('name')} alias={r.get('alias')} "
                f"expression={r.get('expression')} rounding={r.get('rounding')} "
                f"required={r.get('required', True)}"
            )
    if qs.get('group_by'):
        lines.append(f"  Group by       : {', '.join(qs['group_by'])}")
    if qs.get('ordering'):
        lines.append(f"  Ordering       : {qs['ordering']}")
    if qs.get('limit') is not None:
        lines.append(f"  Limit          : {qs['limit']}")
    if qs.get('join_path'):
        lines.append(f"  Join path      : {qs['join_path']}")
    if qs.get('assumptions'):
        lines.append(f"  Assumptions    : {qs['assumptions']}")
    return "\n".join(lines) + "\n"


def _build_benchmark_context(ctx: dict | None) -> str:
    """Render benchmark metadata as a compact SQL-generation contract."""
    if not ctx:
        return ""

    lines = ["BENCHMARK CONTRACT (follow when present):"]
    if ctx.get("dataset_type"):
        lines.append(f"  Dataset type     : {ctx['dataset_type']}")
    if ctx.get("db_id"):
        lines.append(f"  Database ID      : {ctx['db_id']}")
    if ctx.get("question_en"):
        lines.append(f"  English question : {ctx['question_en']}")
    if ctx.get("intent"):
        lines.append(f"  Intent           : {ctx['intent']}")
    if ctx.get("pattern"):
        lines.append(f"  Pattern          : {ctx['pattern']}")
    if ctx.get("tables"):
        lines.append(f"  Tables           : {', '.join(ctx['tables'])}")
    if ctx.get("output_columns"):
        lines.append(
            "  Final columns    : "
            + ", ".join(ctx["output_columns"])
            + " (exact order, no extras, no omissions)"
        )
    if ctx.get("limit") is not None:
        lines.append(f"  Required LIMIT   : {ctx['limit']}")
    if ctx.get("requires_order_by"):
        lines.append("  ORDER BY         : required; infer from question, use stable tie-breakers")
    if ctx.get("order_by_hint"):
        lines.append(f"  ORDER BY hint    : {ctx['order_by_hint']}")
    if ctx.get("semantic_hint"):
        lines.append(f"  Semantic hint    : {ctx['semantic_hint']}")
    if ctx.get("round_numeric_aggregates") is not None:
        lines.append(
            f"  Numeric rounding : aggregate/monetary metrics must use ROUND(..., {ctx['round_numeric_aggregates']})"
        )

    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def _build_repair_context(state: AgentState) -> str:
    """Build repair instructions using structured validation_report when available."""
    if state.validation_report:
        report = state.validation_report

        if not report.get("valid", True):
            errors_txt = "; ".join(
                (
                    f"[{e.get('code', 'ERR')}] {e.get('message', '')} expected={e.get('expected', '')} actual={e.get('actual', '')}"
                    if isinstance(e, dict) else str(e)
                )
                for e in report.get("errors", [])
            )

            return (
                f"\nPREVIOUS SQL FAILED VALIDATION (risk_score={report.get('risk_score', '?')}):\n"
                f"SQL: {state.generated_sql}\n"
                f"ERRORS: {errors_txt}\n"
                f"Repairable: {report.get('repairable', True)}\n"
                "Fix ONLY the listed errors. Do not change output columns or grain.\n"
            )

        warnings = report.get("warnings", [])
        if warnings:
            warnings_txt = "; ".join(
                (
                    f"[{w.get('code', 'WARN')}] {w.get('message', '')} expected={w.get('expected', '')} actual={w.get('actual', '')}"
                    if isinstance(w, dict) else str(w)
                )
                for w in warnings
            )

            return (
                f"\nSEMANTIC VALIDATION WARNINGS FROM PREVIOUS ATTEMPT:\n"
                f"SQL: {state.generated_sql}\n"
                f"WARNINGS: {warnings_txt}\n"
                "These warnings are not necessarily errors. Only adjust the SQL if the warning clearly applies.\n"
                "If this is a PROJECTION warning (e.g. PROJECTION_TOO_WIDE, MISSING_OUTPUT_COLUMN), fix the outer SELECT clause exactly as requested in expected.\n"
            )

    if state.execution_error:
        return (
            f"\nPREVIOUS SQL FAILED:\nSQL: {state.generated_sql}\n"
            f"ERROR: {state.execution_error}\nPlease FIX the SQL based on this error.\n"
        )

    return ""


def _resolve_few_shot_scope(state: AgentState) -> tuple[str | None, str, str | None]:
    """Resolve dataset/split/database filters without leaking evaluation answers."""
    dataset_type = state.dataset_type.lower() if state.dataset_type else None
    split = state.evaluation_options.get("few_shot_split")
    db_id = None

    if dataset_type in ("northwind", "chinook", "chinook_vn"):
        split = split or "fewshot"
        if dataset_type == "chinook_vn":
            dataset_type = "chinook"
    elif dataset_type in ("spider", "bird"):
        split = split or "train"
        db_id = str((state.benchmark_context or {}).get("db_id") or "").strip() or None
    else:
        split = split or "train"

    return dataset_type, split, db_id


def sql_generator_node(state: AgentState) -> AgentState:
    """Node: SQLGenerator with QuerySpec-aware generation and targeted repair."""
    state.generation_attempts += 1

    # 1. Retrieve Few-shot examples
    few_shot_text = ""
    few_shot_diagnostic = {
        "enabled": bool(state.evaluation_options.get("few_shot_enabled", True)),
        "count": 0,
        "example_ids": [],
        "source_db_ids": [],
    }
    if not state.evidence and state.evaluation_options.get("few_shot_enabled", True):
        try:
            from src.rag.few_shot_retriever import FewShotRetriever
            dataset_type, split, db_id = _resolve_few_shot_scope(state)
            
            retriever = FewShotRetriever()
            examples = retriever.retrieve(
                state.user_question, 
                k=state.evaluation_options.get("few_shot_k", 2), 
                dataset_type=dataset_type, 
                split=split,
                db_id=db_id
            )
            if examples:
                few_shot_diagnostic.update({
                    "count": len(examples),
                    "example_ids": [str(ex.get("example_id", "")) for ex in examples],
                    "source_db_ids": [str(ex.get("db_id", "")) for ex in examples],
                })
                few_shot_text = (
                    "BELOW ARE SIMILAR FEW-SHOT EXAMPLES. Examples from another "
                    "database are structural references only; remap every table, "
                    "column, join, and literal to the CURRENT schema/question:\n"
                )
                for i, ex in enumerate(examples):
                    source_db = ex.get("db_id") or "unknown"
                    few_shot_text += (
                        f"Example {i+1} (source DB: {source_db}):\n"
                        f"- Question: {ex['question']}\n- SQL: {ex['sql']}\n\n"
                    )
        except Exception as e:
            log.warning("few_shot_failed", error=str(e))
            few_shot_diagnostic["error"] = str(e)[:500]

    state.telemetry = {**state.telemetry, "few_shot": few_shot_diagnostic}

    # 2. Build schema and system prompt
    schema_text = _build_schema_text_for_prompt(state.schema_context)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text, db_dialect=state.db_dialect)

    # 3. Assemble user prompt sections
    plan_ctx = ""
    if state.plan:
        steps = "\n".join(
            f"Step {s['step_id']}: {s['description']}" for s in state.plan.get("steps", [])
        )
        plan_ctx = f"EXECUTION PLAN:\n{steps}\n"

    effective_query_spec = state.query_spec
    benchmark_cols = (state.benchmark_context or {}).get("output_columns") or []
    if benchmark_cols and not effective_query_spec:
        effective_query_spec = {"output_columns": benchmark_cols}
    benchmark_limit = (state.benchmark_context or {}).get("limit")
    if benchmark_limit is not None:
        if not effective_query_spec:
            effective_query_spec = {}
        if effective_query_spec.get("limit") is None:
            effective_query_spec["limit"] = benchmark_limit

    spec_ctx = _build_spec_context(state.query_spec)
    benchmark_ctx = _build_benchmark_context(state.benchmark_context)
    evidence_ctx = f"BUSINESS RULES / EVIDENCE:\n{state.evidence}\n" if state.evidence else ""
    error_ctx = _build_repair_context(state)

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
{few_shot_text}{evidence_ctx}{benchmark_ctx}{spec_ctx}{plan_ctx}{error_ctx}
CURRENT QUESTION: {state.user_question}

Rely on the SCHEMA, QUERY SPECIFICATION (if any), BUSINESS RULES, and few-shot examples to write the most accurate SQL. Return ONLY JSON.

# 10. Prefilled response (if any)
(None)"""

    raw = ""
    sql = ""
    accepted_sql = ""
    accepted_raw = ""
    fallback_sql = ""
    fallback_raw = ""

    for attempt in range(2):
        if attempt > 0:
            wait = 2 ** attempt  # exponential backoff
            log.info("sql_gen_waiting", seconds=wait, reason="Retry backoff")
            time.sleep(wait)
        try:
            raw = invoke(
                prompt=user_prompt,
                model=config.LLM_MODEL_PRO,
                temperature=0.1,
                max_tokens=4096,
                system_prompt=system_prompt,
                telemetry_label="sql_generator",
            )

            sql = _extract_sql(raw)
            is_safe, issues = _validate_dangerous(sql)

            if not is_safe or not sql:
                error_msg = f"Security check failed or no SQL extracted: {issues}"
                log.warning("sql_extract_failed", raw=raw[:200], sql=sql, issues=issues)
                state.telemetry.setdefault("sql_generation_diagnostics", []).append({
                    "generation_attempt": state.generation_attempts,
                    "local_attempt": attempt + 1,
                    "stage": "extract",
                    "raw_preview": raw[:500],
                    "extracted_sql": sql[:300],
                    "issues": list(issues),
                })
                user_prompt += f"\n\nATTEMPT {attempt+1} FAILED:\nSQL generated: {sql}\nError: {error_msg}\nPlease FIX this."
                continue

            # Self-correction via EXPLAIN (SQLite only — zero network cost)
            if (
                state.evaluation_options.get("self_correction_enabled", True)
                and state.current_db_path
                and state.db_dialect == "sqlite"
            ):
                from src.db import get_db_manager
                from sqlalchemy import text as sa_text
                try:
                    db = get_db_manager(state.current_db_path)
                    with db.engine.connect() as conn:
                        conn.execute(sa_text(f"EXPLAIN QUERY PLAN {sql}"))

                    spec_issues = (
                        _quick_check_query_spec_contract(sql, effective_query_spec)
                        if state.evaluation_options.get("projection_contract_enabled", True)
                        else []
                    )
                    if spec_issues:
                        fallback_sql = sql
                        fallback_raw = raw
                        state.telemetry.setdefault("sql_generation_diagnostics", []).append({
                            "generation_attempt": state.generation_attempts,
                            "local_attempt": attempt + 1,
                            "stage": "query_spec_contract",
                            "sql_preview": sql[:500],
                            "issues": list(spec_issues),
                        })
                        log.warning(
                            "sql_queryspec_contract_failed",
                            attempt=attempt + 1,
                            issues=spec_issues,
                            sql=sql[:160],
                        )
                        user_prompt += (
                            f"\n\nATTEMPT {attempt+1} VIOLATED QUERY SPEC CONTRACT:\n"
                            f"SQL generated: {sql}\n"
                            f"Issues: {'; '.join(spec_issues)}\n"
                            "Regenerate SQL. The final OUTER SELECT must exactly match "
                            "QuerySpec.output_columns in both column names and order. "
                            "Do not add, omit, merge, split, or reorder output columns."
                        )
                        continue
                    accepted_sql = sql
                    accepted_raw = raw
                    log.info("sql_validation_success", sql=sql[:80])
                    break
                except Exception as e:
                    error_msg = str(e).strip()
                    state.telemetry.setdefault("sql_generation_diagnostics", []).append({
                        "generation_attempt": state.generation_attempts,
                        "local_attempt": attempt + 1,
                        "stage": "explain",
                        "sql_preview": sql[:500],
                        "error": error_msg[:500],
                    })
                    log.warning("sql_self_correction_triggered", attempt=attempt + 1, error=error_msg)
                    user_prompt += (
                        f"\n\nATTEMPT {attempt+1} FAILED with Database error:\n"
                        f"SQL generated: {sql}\nError: {error_msg}\n"
                        "DO NOT use columns or tables that do not exist in the schema."
                    )
                    continue
            else:
                spec_issues = (
                    _quick_check_query_spec_contract(sql, effective_query_spec)
                    if state.evaluation_options.get("projection_contract_enabled", True)
                    else []
                )
                if spec_issues:
                    fallback_sql = sql
                    fallback_raw = raw
                    log.warning(
                        "sql_queryspec_contract_failed_no_db",
                        attempt=attempt + 1,
                        issues=spec_issues,
                        sql=sql[:160],
                    )
                    user_prompt += (
                        f"\n\nATTEMPT {attempt+1} VIOLATED QUERY SPEC CONTRACT:\n"
                        f"SQL generated: {sql}\n"
                        f"Issues: {'; '.join(spec_issues)}\n"
                        "Regenerate SQL. The final OUTER SELECT must exactly match "
                        "QuerySpec.output_columns in both column names and order."
                    )
                    continue

            accepted_sql = sql
            accepted_raw = raw
            break  # Cloud DB or no DB → trust validator

        except Exception as e:
            log.warning("sql_gen_retry", attempt=attempt + 1, error=str(e))
            state.telemetry.setdefault("sql_generation_diagnostics", []).append({
                "generation_attempt": state.generation_attempts,
                "local_attempt": attempt + 1,
                "stage": "invoke",
                "error": str(e)[:500],
            })
            if attempt == 1:
                raw = ""

    # Final extraction
    if accepted_sql:
        sql = accepted_sql
        raw = accepted_raw
    elif fallback_sql:
        sql = fallback_sql
        raw = fallback_raw
    else:
        sql = ""

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
