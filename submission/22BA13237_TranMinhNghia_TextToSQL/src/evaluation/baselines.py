"""Single-agent baselines that share the production model and database layer."""

import json
import re
import time
from copy import deepcopy

from src.agents.llm_router import invoke
from src.agents.state import AgentState
from src.config import config
from src.db import get_db_manager
from src.evaluation.telemetry import record_node_timing, telemetry_run


def _schema_text(db) -> str:
    schema = db.get_schema()
    lines = []
    for table in schema.tables:
        columns = ", ".join(
            f"{column['name']} {column.get('type') or 'TEXT'}"
            for column in table.columns
        )
        lines.append(f"Table {table.table_name}({columns})")
    if schema.relationships:
        lines.append("Relationships:")
        for relationship in schema.relationships:
            lines.append(
                f"- {relationship['from_table']}.{relationship['from_column']} -> "
                f"{relationship['to_table']}.{relationship['to_column']}"
            )
    return "\n".join(lines)


def _extract_sql(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:sql|json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("sql"):
            return str(parsed["sql"]).strip()
    except json.JSONDecodeError:
        pass
    match = re.search(r"\b(?:SELECT|WITH)\b[\s\S]*", text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _benchmark_contract_text(context: dict | None) -> str:
    """Give baselines the same non-gold benchmark contract as the full system."""
    if not context:
        return ""
    lines = []
    if context.get("tables"):
        lines.append("Expected tables: " + ", ".join(context["tables"]))
    if context.get("output_columns"):
        lines.append("Required final columns: " + ", ".join(context["output_columns"]))
    if context.get("limit") is not None:
        lines.append(f"Required LIMIT: {context['limit']}")
    if context.get("requires_order_by"):
        lines.append("ORDER BY is required.")
    if context.get("order_by_hint"):
        lines.append("ORDER BY hint: " + str(context["order_by_hint"]))
    if context.get("semantic_hint"):
        lines.append("Semantic hint: " + str(context["semantic_hint"]))
    if context.get("round_numeric_aggregates") is not None:
        lines.append(
            f"Round relevant numeric aggregates to {context['round_numeric_aggregates']} decimals."
        )
    return "BENCHMARK CONTRACT:\n" + "\n".join(lines) if lines else ""


async def arun_baseline_query(
    *,
    question: str,
    session_id: str,
    db_path: str,
    baseline: str,
    dataset_type: str | None = None,
    benchmark_context: dict | None = None,
) -> AgentState:
    """Run a one-call baseline and return the normal AgentState contract."""
    db = get_db_manager(db_path or config.DB_PATH)
    schema = _schema_text(db)
    benchmark_contract = _benchmark_contract_text(benchmark_context)
    if baseline == "zero_shot":
        system_prompt = "You translate natural-language questions into read-only SQLite SQL. Return SQL only."
        prompt = f"SCHEMA:\n{schema}\n\n{benchmark_contract}\n\nQUESTION: {question}"
    elif baseline == "structured":
        system_prompt = (
            "You are a Text-to-SQL expert. Use only provided tables and columns. "
            "Generate one read-only SELECT/WITH query, preserve literal values, use explicit JOIN keys, "
            "and return strict JSON: {\"sql\": \"SELECT ...\"}."
        )
        prompt = (
            f"DATABASE DIALECT: SQLite\nSCHEMA:\n{schema}\n\n"
            f"{benchmark_contract}\n\nQUESTION: {question}\nReturn the smallest correct query."
        )
    else:
        raise ValueError(f"Unsupported baseline: {baseline}")

    state = AgentState(
        user_question=question,
        session_id=session_id,
        current_db_path=db_path or config.DB_PATH,
        dataset_type=dataset_type,
        benchmark_context=benchmark_context or {},
        evaluation_profile=f"single_{baseline}",
        evaluation_options={"baseline": baseline},
    )

    with telemetry_run(session_id) as collector:
        llm_started = time.perf_counter()
        try:
            raw = invoke(
                prompt=prompt,
                model=config.LLM_MODEL_PRO,
                temperature=0.0,
                max_tokens=4096,
                system_prompt=system_prompt,
                telemetry_label=f"single_{baseline}",
            )
            sql = _extract_sql(raw)
        finally:
            record_node_timing("single_agent", (time.perf_counter() - llm_started) * 1000)

        state.generated_sql = sql
        if not sql:
            state.error = "Baseline model did not return SQL"
            state.execution_error = state.error
        else:
            execution_started = time.perf_counter()
            result = db.execute_query(sql)
            record_node_timing("executor", (time.perf_counter() - execution_started) * 1000)
            state.execution_time_ms = result.execution_time_ms
            state.execution_error = result.error
            if result.error:
                state.error = result.error
            else:
                state.query_result = {
                    "sql": result.sql,
                    "columns": result.columns,
                    "rows": result.rows,
                    "row_count": result.row_count,
                    "execution_time_ms": result.execution_time_ms,
                }
                state.formatted_answer = {
                    "summary": f"Truy vấn trả về {result.row_count} dòng dữ liệu.",
                    "rows": result.rows,
                    "columns": result.columns,
                    "row_count": result.row_count,
                    "from_cache": False,
                }
                state.current_step = "baseline_complete"

    state.telemetry = deepcopy(collector)
    return state
