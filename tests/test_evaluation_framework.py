"""Tests for deterministic evaluation metrics and telemetry."""

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TEST_DIR = Path(__file__).resolve().parents[1] / "test"
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from evaluation_metrics import (  # noqa: E402
    bootstrap_binary_ci,
    compare_execution,
    compare_structure,
    execute_sql,
    percentile,
    summarize_results,
)
from evaluate_cache import threshold_sweep  # noqa: E402
from src.evaluation.profiles import get_profile_options  # noqa: E402
from src.evaluation.telemetry import record_llm_call, snapshot, telemetry_run  # noqa: E402


def test_strict_and_relaxed_execution_are_separate():
    gold = {"ok": True, "columns": ["id", "name"], "rows": [(1, "Alice")]}
    generated = {"ok": True, "columns": ["customer", "key"], "rows": [("Alice", 1)]}

    result = compare_execution(gold, generated)

    assert result["strict_ex"] is False
    assert result["label_exact_match"] is False
    assert result["relaxed_ex"] is True


def test_strict_execution_ignores_column_alias_labels():
    gold = {"ok": True, "columns": ["CustomerID", "CompanyName"], "rows": [(1, "Alice")]}
    generated = {"ok": True, "columns": ["id", "name"], "rows": [(1, "Alice")]}

    result = compare_execution(gold, generated)

    assert result["strict_ex"] is True
    assert result["label_exact_match"] is False
    assert result["relaxed_ex"] is True


def test_strict_execution_preserves_column_order_and_duplicate_rows():
    gold = {"ok": True, "columns": ["id", "name"], "rows": [(1, "A"), (1, "A")]}
    swapped = {"ok": True, "columns": ["name", "id"], "rows": [("A", 1), ("A", 1)]}
    missing_duplicate = {"ok": True, "columns": ["id", "name"], "rows": [(1, "A")]}

    assert compare_execution(gold, swapped)["strict_ex"] is False
    assert compare_execution(gold, swapped)["relaxed_ex"] is True
    assert compare_execution(gold, missing_duplicate)["strict_ex"] is False


def test_execute_sql_reports_success_and_failure(tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
    connection.execute("INSERT INTO items VALUES (1, 'A')")
    connection.commit()
    connection.close()

    success = execute_sql("SELECT id, name FROM items", str(db_path))
    failure = execute_sql("SELECT missing FROM items", str(db_path))

    assert success == {"ok": True, "columns": ["id", "name"], "rows": [(1, "A")], "error": None}
    assert failure["ok"] is False
    assert failure["error"]


def test_percentile_and_bootstrap_are_deterministic():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert bootstrap_binary_ci([True, True, False, True], samples=100, seed=7) == bootstrap_binary_ci(
        [True, True, False, True], samples=100, seed=7
    )


def test_telemetry_aggregates_usage():
    class FakeResponse:
        usage_metadata = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
        response_metadata = {}

    with telemetry_run("unit") as collector:
        record_llm_call(
            provider="test",
            model="fake",
            response=FakeResponse(),
            elapsed_ms=12.5,
            label="sql_generator",
        )
        current = snapshot()
        assert current["total_tokens"] == 14
        assert len(current["llm_calls"]) == 1

    assert collector["input_tokens"] == 10
    assert collector["output_tokens"] == 4
    assert collector["elapsed_ms"] >= 0


def test_summary_contains_latency_tokens_and_intents():
    rows = [
        {
            "intent": "simple", "strict_ex": True, "relaxed_ex": True,
            "label_exact_match": True,
            "execution_success": True, "structure_match": True, "retry_count": 0,
            "latency_ms": 100, "telemetry": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "llm_calls": [{}]},
        },
        {
            "intent": "join", "strict_ex": False, "relaxed_ex": True,
            "label_exact_match": False,
            "execution_success": True, "structure_match": False, "retry_count": 1,
            "latency_ms": 300, "telemetry": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25, "llm_calls": [{}, {}]},
        },
    ]

    summary = summarize_results(rows)

    assert summary["strict_ex"] == 0.5
    assert summary["label_exact_match"] == 0.5
    assert summary["relaxed_ex"] == 1.0
    assert summary["latency_ms"]["p50"] == 200.0
    assert summary["tokens"]["total"] == 40
    assert summary["llm_calls"]["total"] == 3
    assert set(summary["by_intent"]) == {"simple", "join"}


def test_profiles_are_reproducible_and_cache_is_off_for_accuracy():
    assert get_profile_options("full_no_cache")["cache_enabled"] is False
    assert get_profile_options("no_rag")["few_shot_enabled"] is False
    assert get_profile_options("no_planner")["planner_enabled"] is False
    assert get_profile_options("no_validator")["validator_enabled"] is False
    assert get_profile_options("no_validator")["self_correction_enabled"] is False
    assert get_profile_options("auto_bypass")["schema_pruning_mode"] == "auto"
    assert get_profile_options("forced_pruning")["schema_pruning_mode"] == "force"
    with pytest.raises(ValueError):
        get_profile_options("unknown")


def test_dynamic_bypass_and_forced_pruning_call_expected_agents(monkeypatch):
    from src.rag import schema_indexer
    from src.agents import column_pruner, table_selector

    calls = {"selector": 0, "pruner": 0}
    tables = [
        SimpleNamespace(
            table_name=f"Table{i}",
            columns=[{"name": "id", "type": "INTEGER"}, {"name": "value", "type": "TEXT"}],
        )
        for i in range(11)
    ]
    schema = SimpleNamespace(tables=tables, relationships=[])
    db = SimpleNamespace(db_path="small.sqlite", get_schema=lambda: schema)

    monkeypatch.setattr(schema_indexer, "_get_semantic_descriptions", lambda _: {})

    def select_tables(*_args, **_kwargs):
        calls["selector"] += 1
        return [table.table_name for table in tables]

    def prune_columns(*_args, **_kwargs):
        calls["pruner"] += 1
        return {table.table_name: ["id"] for table in tables}

    monkeypatch.setattr(table_selector, "select_tables_for_query", select_tables)
    monkeypatch.setattr(column_pruner, "prune_columns_for_query", prune_columns)

    auto_context = schema_indexer.get_schema_context_for_query(
        "test",
        db=db,
        pruning_mode="auto",
    )
    assert calls == {"selector": 0, "pruner": 0}
    assert "value (TEXT)" in auto_context

    forced_context = schema_indexer.get_schema_context_for_query(
        "test",
        db=db,
        pruning_mode="force",
    )
    assert calls == {"selector": 1, "pruner": 1}
    assert "value (TEXT)" not in forced_context

    with pytest.raises(ValueError):
        schema_indexer.get_schema_context_for_query("test", db=db, pruning_mode="invalid")


def test_cache_case_file_contains_90_lookups():
    path = Path(__file__).resolve().parents[1] / "test" / "cache_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    assert len(data["groups"]) == 30
    assert all({"id", "base", "paraphrase", "hard_negative"} <= set(group) for group in data["groups"])
    assert len(data["groups"]) * 3 == 90


def test_cache_threshold_sweep_reuses_embeddings(monkeypatch):
    import src.memory.semantic_cache as cache_module

    calls = []

    def fake_embed(text, task_type="RETRIEVAL_DOCUMENT"):
        calls.append((text, task_type))
        return [1.0, 0.0, 0.0], {"backend": "fake", "model": "fake", "dimension": 3}

    monkeypatch.setattr(cache_module, "embed_single_with_metadata", fake_embed)
    groups = [
        {"id": "1", "base": "Hiển thị 5 đơn hàng đầu tiên", "paraphrase": "Liệt kê 5 đơn hàng đầu tiên", "hard_negative": "Hiển thị 10 đơn hàng đầu tiên"},
        {"id": "2", "base": "Đếm tổng số khách hàng", "paraphrase": "Có bao nhiêu khách hàng", "hard_negative": "Đếm tổng số nhân viên"},
    ]

    reports = threshold_sweep(groups, "unit")

    assert len(reports) == 12
    assert all(report["exact_recall"] == 1.0 for report in reports)
    assert len(calls) <= 6


def test_sql_ast_structure_when_sqlglot_is_installed():
    pytest.importorskip("sqlglot")
    result = compare_structure(
        "SELECT c.name, COUNT(*) FROM customers c JOIN orders o ON c.id=o.customer_id GROUP BY c.name",
        "SELECT c.name, COUNT(o.id) FROM customers AS c JOIN orders AS o ON c.id=o.customer_id GROUP BY c.name",
    )
    assert result["structure_match"] is True
    assert result["structure_score"] == 1.0
