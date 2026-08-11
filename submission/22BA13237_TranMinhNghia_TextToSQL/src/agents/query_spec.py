"""
QuerySpec Agent — produces a structured query specification from a natural-language question.

The specification acts as a contract between the Planner and the SQL Generator:
  - Defines exactly which columns to SELECT (projection).
  - Identifies the grain (one-row-per-what?).
  - Lists required JOINs with FK paths.
  - Specifies filters, aggregations, ordering, and LIMIT.

By grounding the generator on a spec instead of a free-form plan, we eliminate
the most common failure modes observed in evaluation:
  - Projection mismatch (wrong / extra columns).
  - Grain duplication (fan-out on 1-to-many JOIN).
  - Wrong aggregation column (pre-computed field vs. function).
  - Implicit DISTINCT / missing DISTINCT.
"""

from __future__ import annotations

import json
import re
import structlog

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal

from src.agents.state import AgentState
from src.agents.llm_router import invoke
from src.config import config

log = structlog.get_logger("query_spec")


# ── Data models ──────────────────────────────────────────────────────────────

class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_table: str
    right_table: str
    left_key: str
    right_key: str
    join_type: Literal["INNER", "LEFT", "RIGHT", "CROSS"] = "INNER"


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str            # e.g. "Orders.ShipCountry"
    operator: str          # =, !=, >, <, LIKE, IN, BETWEEN, IS NULL …
    value: str             # literal value extracted from question


class AggregationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: Literal["COUNT", "SUM", "AVG", "MAX", "MIN"]
    column: str            # table.column or * for COUNT(*)
    alias: str             # output column name


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    direction: Literal["ASC", "DESC"] = "ASC"


class DateConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    operator: str
    value: str
    meaning: str

class MetricRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    expression: str
    alias: str
    rounding: Optional[int] = None
    required: bool = True

class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str                              # one-line summary of user intent
    output_columns: list[str]               # exact columns/expressions to SELECT, in order
    output_grain: str                        # "one row per <entity>"
    source_tables: list[str]                # base tables needed
    join_path: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    aggregations: list[AggregationSpec] = Field(default_factory=list)
    metric_rules: list[MetricRule] = Field(default_factory=list)
    date_semantics: list[DateConstraint] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    ordering: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    deduplication: Literal["none", "DISTINCT", "GROUP_BY"] = "none"
    projection_policy: Literal["exact", "minimal", "entity_default"] = "exact"
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ── System prompt ─────────────────────────────────────────────────────────────

_SPEC_SYSTEM = """# Role
You are a Query Specification Analyst in a Text-to-SQL pipeline.
Your ONLY job is to produce a strict JSON QuerySpec describing what SQL to build.
You do NOT write SQL. The SQL Generator will receive your spec and follow it exactly.

# Schema
{schema_context}

# Critical rules
0. If Benchmark contract / hints provides Required final output columns, copy them exactly
   into output_columns in the same order. These benchmark columns override your inference.
1. output_columns: list ONLY the columns/aggregates explicitly requested by the user.
   Do NOT add IDs, timestamps, or "useful" metadata unless asked.
   For benchmark queries, output_columns must be minimal and exact.
   If the user asks for a list of entities (e.g. employees), infer ONLY their minimal display identity columns (e.g. FirstName, LastName).
   Never request all columns from an entity table unless explicitly asked. Do NOT include Photo, Notes, Address, etc.
   For aggregate or calculated metrics, output_columns must contain the final exposed alias names, not long SQL expressions.
   Example: use "Revenue" or "TotalSpent" in output_columns, and define the exact formula in metric_rules.
   If metric_rules contains alias="Revenue", then output_columns should include "Revenue".
2. output_grain: state "one row per <X>" clearly. If a JOIN creates duplicates, add DISTINCT or GROUP BY to deduplication.
3. join_path: use EXACT FK/PK column names from the schema. Specify join_type (prefer LEFT when outer semantics are needed).
4. filters: extract LITERAL values from the question — do not invent values.
5. aggregations: check if a pre-computed column already exists before suggesting AVG()/SUM().
6. deduplication: set to "DISTINCT" when selecting a parent entity via a child JOIN; "GROUP_BY" when aggregating; "none" otherwise.
7. assumptions: record any ambiguity or interpretation decision so the generator and validator can verify.
8. confidence: 0.0–1.0. Use < 0.7 when the question is ambiguous or schema coverage is uncertain.
9. date_semantics: explicitly identify date columns and their meaning if the schema has multiple dates.
   For example, distinguish between a "creation date" vs a "shipping/completion date" based on the user's intent.
   Do not mix them up.
10. metric_rules: for business metrics such as revenue, total spending, order value, discount amount, or average order value,
    define the exact expression, alias, and rounding requirement.
    Use the business rules/evidence provided by the system to determine the correct formula.
    Do not invent formulas if the evidence is missing.
    For monetary outputs, set rounding to 2 when the question or benchmark convention expects rounded money values.
    Use COUNT(DISTINCT ...) when counting parent entities after joining to child/detail tables.
11. Quantified comparisons are exact: greater than ANY uses > MIN, greater than ALL uses > MAX,
    less than ANY uses < MAX, and less than ALL uses < MIN.
12. If one entity must match both distinct values in child rows, plan INTERSECT or
    GROUP BY/HAVING COUNT(DISTINCT ...); never require one scalar row to equal both values.
13. Preserve schema-native comparison and ordering semantics. Do not add CAST merely because values look numeric unless conversion is explicitly required.
14. For anti-membership (entities that do not have X), exclude by the entity key with NOT EXISTS or key NOT IN; never subtract projected display attributes.
15. Use INNER JOIN by default. Choose LEFT JOIN only for explicit missing/none/zero-count inclusion semantics.

# Output format
Return ONLY a raw, flat JSON object matching the fields of QuerySpec.
The JSON object MUST strictly adhere to this structure:
{{
  "intent": "string (one-line summary)",
  "output_columns": ["string (exact columns/expressions to SELECT)"],
  "output_grain": "string (e.g. 'one row per order')",
  "source_tables": ["string"],
  "join_path": [
    {{
      "left_table": "string",
      "right_table": "string",
      "left_key": "string",
      "right_key": "string",
      "join_type": "INNER|LEFT|RIGHT|CROSS"
    }}
  ],
  "filters": [
    {{
      "column": "string",
      "operator": "string (e.g. =, !=, >, <, IN, LIKE)",
      "value": "string"
    }}
  ],
  "aggregations": [
    {{
      "function": "COUNT|SUM|AVG|MAX|MIN",
      "column": "string",
      "alias": "string"
    }}
  ],
    "metric_rules": [
    {{
      "name": "string (e.g. revenue_after_discount)",
      "expression": "string (exact metric expression to use)",
      "alias": "string (final output alias)",
      "rounding": 2,
      "required": true
    }}
  ],
  "date_semantics": [
    {{
      "column": "string",
      "operator": "string",
      "value": "string",
      "meaning": "string"
    }}
  ],
  "group_by": ["string"],
  "ordering": [
    {{
      "column": "string",
      "direction": "ASC|DESC"
    }}
  ],
  "limit": 10,
  "deduplication": "none|DISTINCT|GROUP_BY",
  "projection_policy": "exact|minimal|entity_default",
  "assumptions": ["string"],
  "confidence": 0.95
}}

CRITICAL RULES:
- Do NOT wrap the object inside a root key such as "query_spec", "QuerySpec", "spec", "data", or "result".
- Do NOT return {{"query_spec": {{...}}}}.
- Do NOT use markdown fences.
- Do NOT add explanation text before or after the JSON.
- Do NOT add fields that are not defined in the QuerySpec schema.
- If uncertain, use empty arrays, null values, and put uncertainty in "assumptions".
"""

_SPEC_USER = """Question: {question}

Evidence / Business rules: {evidence}

Benchmark contract / hints:
{benchmark_context}

Return a QuerySpec JSON for this question.
"""


# ── Helper ────────────────────────────────────────────────────────────────────

from typing import Any

WRAPPER_KEYS = {
    "query_spec",
    "QuerySpec",
    "spec",
    "data",
    "result",
}


def _strip_markdown_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> str:
    """Extract the largest plausible JSON object from LLM output."""
    text = _strip_markdown_fence(text)

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    raise json.JSONDecodeError("No JSON object found", text, 0)


def _unwrap_query_spec(parsed: Any) -> Any:
    """Unwrap common LLM wrapper keys while preserving strict QuerySpec validation."""
    if not isinstance(parsed, dict):
        return parsed

    if len(parsed) == 1:
        key = next(iter(parsed.keys()))
        value = parsed[key]
        if key in WRAPPER_KEYS and isinstance(value, dict):
            return value

    return parsed


def _safe_parse(text: str) -> dict:
    raw_json = _extract_json_object(text)
    parsed = json.loads(raw_json)
    parsed = _unwrap_query_spec(parsed)

    if not isinstance(parsed, dict):
        raise ValueError("QuerySpec output must be a JSON object")

    return parsed


def _build_benchmark_context(ctx: dict | None) -> str:
    """Render non-answer benchmark metadata as binding hints for QuerySpec."""
    if not ctx:
        return "None."

    lines = []
    if ctx.get("dataset_type"):
        lines.append(f"- Dataset type: {ctx['dataset_type']}")
    if ctx.get("db_id"):
        lines.append(f"- Database ID: {ctx['db_id']}")
    if ctx.get("question_en"):
        lines.append(f"- English question: {ctx['question_en']}")
    if ctx.get("intent"):
        lines.append(f"- Expected intent: {ctx['intent']}")
    if ctx.get("pattern"):
        lines.append(f"- Expected pattern: {ctx['pattern']}")
    if ctx.get("tables"):
        lines.append(f"- Expected tables: {', '.join(ctx['tables'])}")
    if ctx.get("output_columns"):
        lines.append(
            "- Required final output columns, exact order: "
            + ", ".join(ctx["output_columns"])
        )
    if ctx.get("limit") is not None:
        lines.append(f"- Required LIMIT: {ctx['limit']}")
    if ctx.get("requires_order_by"):
        lines.append("- ORDER BY is required; infer the stable ordering from the question.")
    if ctx.get("order_by_hint"):
        lines.append(f"- ORDER BY hint: {ctx['order_by_hint']}")
    if ctx.get("semantic_hint"):
        lines.append(f"- Semantic hint: {ctx['semantic_hint']}")
    if ctx.get("round_numeric_aggregates") is not None:
        lines.append(
            f"- Round numeric aggregate/monetary metrics to {ctx['round_numeric_aggregates']} decimals."
        )

    return "\n".join(lines) if lines else "None."


# ── Node ──────────────────────────────────────────────────────────────────────

def query_spec_node(state: AgentState) -> AgentState:
    """
    LangGraph node — produces a QuerySpec and stores it in state.query_spec.
    Runs after orchestrator routing (replaces or precedes query_planner for
    aggregate/join/complex intents).
    """
    log.info("query_spec_run", question=state.user_question[:80])

    schema_ctx = state.schema_context or "No schema available."
    evidence = state.evidence or "None."
    benchmark_context = _build_benchmark_context(state.benchmark_context)

    user_prompt = _SPEC_USER.format(
        question=state.user_question,
        evidence=evidence,
        benchmark_context=benchmark_context,
    )
    system_prompt = _SPEC_SYSTEM.format(schema_context=schema_ctx)

    raw = ""
    try:
        raw = invoke(
            prompt=user_prompt,
            model=config.LLM_MODEL_PRO,
            temperature=0.0,
            max_tokens=4096,
            system_prompt=system_prompt,
            telemetry_label="query_spec",
        )
        spec_dict = _safe_parse(raw)
        benchmark_cols = (state.benchmark_context or {}).get("output_columns") or []
        benchmark_tables = (state.benchmark_context or {}).get("tables") or []
        benchmark_limit = (state.benchmark_context or {}).get("limit")
        if benchmark_cols:
            spec_dict["output_columns"] = benchmark_cols
            spec_dict["projection_policy"] = "exact"
        if benchmark_tables and not spec_dict.get("source_tables"):
            spec_dict["source_tables"] = benchmark_tables
        if benchmark_limit is not None:
            spec_dict["limit"] = benchmark_limit
        
        try:
            raw_parsed = json.loads(_extract_json_object(raw))
            if isinstance(raw_parsed, dict) and len(raw_parsed) == 1:
                key = next(iter(raw_parsed.keys()))
                if key in WRAPPER_KEYS and isinstance(raw_parsed[key], dict):
                    state.telemetry.setdefault("query_spec_unwrapped", 0)
                    state.telemetry["query_spec_unwrapped"] += 1
                    log.warning("query_spec_wrapper_unwrapped", wrapper_key=key)
        except Exception:
            pass

        spec = QuerySpec(**spec_dict)
        state.query_spec = spec.model_dump()
        state.query_spec_failed = False

        log.info(
            "query_spec_produced",
            grain=spec.output_grain,
            columns=spec.output_columns,
            confidence=spec.confidence,
            assumptions=spec.assumptions,
        )

    except Exception as exc:
        log.warning("query_spec_failed", error=str(exc), raw=raw[:200])
        state.query_spec_failed = True
        state.query_spec = None

    state.next_agent = "sql_generator"
    return state
