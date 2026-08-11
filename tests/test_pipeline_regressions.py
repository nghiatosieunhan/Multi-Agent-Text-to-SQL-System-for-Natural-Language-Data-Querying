"""Regression tests for benchmark-critical pipeline behavior."""

from langgraph.graph import END

from src.agents.state import AgentState


def test_sql_prompt_does_not_require_implicit_limit():
    from src.agents.sql_generator import SYSTEM_PROMPT_TEMPLATE

    prompt = SYSTEM_PROMPT_TEMPLATE.format(schema="TABLE Products", db_dialect="sqlite")

    assert "append LIMIT 20" not in prompt
    assert "Never add a safety/display LIMIT" in prompt


def test_sql_prompt_requires_exact_projection():
    from src.agents.sql_generator import SYSTEM_PROMPT_TEMPLATE
    from src.agents.query_spec import _SPEC_SYSTEM

    prompt = SYSTEM_PROMPT_TEMPLATE.format(schema="TABLE Products", db_dialect="sqlite")

    assert "SELECT only the columns or aggregates explicitly requested" in prompt
    assert "SELECT BOTH Primary Key ID" not in prompt
    assert "BENCHMARK CONTRACT" in prompt
    assert "Do not CAST a comparison" in prompt
    assert "exclude entities by their stable primary/foreign key" in prompt
    assert "Use INNER JOIN by default" in prompt
    assert "Preserve schema-native comparison" in _SPEC_SYSTEM


def test_sql_extractor_accepts_common_model_variants():
    from src.agents.sql_generator import _extract_sql

    assert _extract_sql('{"SQL": "SELECT 1"}') == "SELECT 1"
    assert _extract_sql('{"result": {"query": "WITH x AS (SELECT 1) SELECT * FROM x"}}') == (
        "WITH x AS (SELECT 1) SELECT * FROM x"
    )
    assert _extract_sql("Here is the query:\nSELECT ProductName FROM Products") == (
        "SELECT ProductName FROM Products"
    )


def test_spider_few_shot_scope_uses_train_split_and_current_database():
    from src.agents.sql_generator import _resolve_few_shot_scope

    state = AgentState(
        user_question="How many flights arrive in Aberdeen?",
        dataset_type="spider",
        benchmark_context={"db_id": "flight_2"},
    )

    assert _resolve_few_shot_scope(state) == ("spider", "train", "flight_2")


def test_chinook_vn_few_shot_scope_uses_shared_chinook_store():
    from src.agents.sql_generator import _resolve_few_shot_scope

    state = AgentState(user_question="Tat ca bai hat", dataset_type="chinook_vn")

    assert _resolve_few_shot_scope(state) == ("chinook", "fewshot", None)


def test_single_agent_baseline_receives_same_benchmark_contract():
    from src.evaluation.baselines import _benchmark_contract_text

    text = _benchmark_contract_text({
        "tables": ["Orders"],
        "output_columns": ["OrderID"],
        "limit": 10,
        "requires_order_by": True,
    })

    assert "Expected tables: Orders" in text
    assert "Required final columns: OrderID" in text
    assert "Required LIMIT: 10" in text


def test_value_grounding_matches_database_spelling(tmp_path):
    import sqlite3
    from src.db.database import DatabaseManager

    db_path = tmp_path / "values.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE Addresses (state TEXT)")
        conn.execute("INSERT INTO Addresses VALUES ('NorthCarolina')")

    db = DatabaseManager(str(db_path))
    matches = db.find_matching_text_values("Students in North Carolina")

    assert matches == [
        {"table": "Addresses", "column": "state", "value": "NorthCarolina"}
    ]


def test_spider_few_shot_falls_back_to_cross_database_examples():
    from types import SimpleNamespace
    from src.rag.few_shot_retriever import FewShotRetriever

    class FakeVectorStore:
        def __init__(self):
            self.filters = []

        def similarity_search_with_score(self, text, k, filter, fetch_k):
            self.filters.append(filter)
            if filter and filter.get("db_id") == "flight_2":
                return []
            doc = SimpleNamespace(
                page_content="Which airport has the most flights?",
                metadata={
                    "sql": "SELECT airport_id FROM flights GROUP BY airport_id",
                    "dataset": "spider",
                    "split": "train",
                    "db_id": "flight_1",
                    "example_id": "1",
                },
            )
            return [(doc, 0.1)]

    store = FakeVectorStore()
    retriever = object.__new__(FewShotRetriever)
    retriever._get_db = lambda: store

    examples = retriever.retrieve(
        "Which airport has the most flights?",
        dataset_type="spider",
        split="train",
        db_id="flight_2",
        k=1,
    )

    assert store.filters == [
        {"dataset": "spider", "split": "train", "db_id": "flight_2"},
        {"dataset": "spider", "split": "train"},
    ]
    assert examples[0]["cross_db"] is True
    assert examples[0]["db_id"] == "flight_1"


def test_benchmark_context_renders_required_columns():
    from src.agents.sql_generator import _build_benchmark_context

    text = _build_benchmark_context({
        "question_en": "List orders.",
        "intent": "simple",
        "tables": ["Orders"],
        "output_columns": ["OrderID", "OrderDate"],
    })

    assert "Final columns" in text
    assert "OrderID, OrderDate" in text
    assert "exact order" in text


def test_explicit_star_projection_contract_is_allowed():
    from src.agents.sql_generator import _quick_check_query_spec_contract
    from src.agents.validator import _validate_projection

    query_spec = {"output_columns": ["*"]}

    assert _quick_check_query_spec_contract("SELECT * FROM KhachHang", query_spec) == []
    assert _validate_projection("SELECT * FROM KhachHang", query_spec) == []


def test_star_projection_is_rejected_when_explicit_columns_are_expected():
    from src.agents.sql_generator import _quick_check_query_spec_contract
    from src.agents.validator import _validate_projection

    query_spec = {"output_columns": ["Ten"]}

    assert _quick_check_query_spec_contract("SELECT * FROM BaiHat", query_spec)
    warnings = _validate_projection("SELECT * FROM BaiHat", query_spec)
    assert any(item["code"] == "SELECT_STAR_USED" for item in warnings)


def test_projection_contract_ignores_table_qualifiers_inside_aggregates():
    from src.agents.sql_generator import _quick_check_query_spec_contract
    from src.agents.validator import _validate_projection

    sql = "SELECT COUNT(DISTINCT T1.Location) FROM shop AS T1"
    query_spec = {"output_columns": ["COUNT(DISTINCT Location)"]}

    assert _quick_check_query_spec_contract(sql, query_spec) == []
    assert _validate_projection(sql, query_spec) == []


def test_sql_generation_failure_does_not_emit_arbitrary_fallback(monkeypatch):
    import time
    import src.agents.sql_generator as sql_generator

    monkeypatch.setattr(sql_generator, "invoke", lambda *args, **kwargs: "not valid SQL")
    monkeypatch.setattr(time, "sleep", lambda _: None)
    state = AgentState(
        user_question="Liệt kê khách hàng",
        schema_context="Table: Customers\nColumns: CustomerID, CompanyName",
        evaluation_options={"few_shot_enabled": False},
    )

    result = sql_generator.sql_generator_node(state)

    assert result.generated_sql == ""
    assert result.next_agent == "error"
    assert result.current_step == "sql_generation_failed"
    assert result.error == "SQL generation failed after all attempts"


def test_orchestrator_error_terminates_without_end_key_error(monkeypatch):
    import src.graph as graph_module

    def route_to_orchestrator(state):
        state.next_agent = "orchestrator"
        return state

    def fail_orchestrator(state):
        state.error = "orchestrator unavailable"
        return state

    monkeypatch.setattr(graph_module, "router_node", route_to_orchestrator)
    monkeypatch.setattr(graph_module, "orchestrator_node", fail_orchestrator)

    workflow = graph_module.build_graph().compile()
    result = workflow.invoke(AgentState(user_question="test"))

    assert result["error"] == "orchestrator unavailable"
    assert result["generated_sql"] == ""
    assert END == "__end__"


def test_query_planner_fallback_reaches_sql_generator(monkeypatch):
    import src.graph as graph_module

    def route_to_orchestrator(state):
        state.next_agent = "orchestrator"
        return state

    def route_to_planner(state):
        state.plan_needed = True
        state.next_agent = "query_planner"
        return state

    def fake_planner(state):
        state.plan = {"steps": []}
        state.next_agent = "sql_generator"
        return state

    def fake_generator(state):
        state.generated_sql = "SELECT 1;"
        state.next_agent = "executor"
        return state

    def fake_validator(state):
        state.next_agent = "executor"
        return state

    def fake_executor(state):
        state.query_result = {
            "sql": state.generated_sql,
            "columns": ["1"],
            "rows": [{"1": 1}],
            "row_count": 1,
            "execution_time_ms": 0,
        }
        state.next_agent = "result_formatter"
        return state

    def fake_formatter(state):
        state.formatted_answer = {"summary": "ok"}
        return state

    monkeypatch.setattr(graph_module, "router_node", route_to_orchestrator)
    monkeypatch.setattr(graph_module, "orchestrator_node", route_to_planner)
    monkeypatch.setattr(graph_module, "query_planner_node", fake_planner)
    monkeypatch.setattr(graph_module, "sql_generator_node", fake_generator)
    monkeypatch.setattr(graph_module, "validator_node", fake_validator)
    monkeypatch.setattr(graph_module, "executor_node", fake_executor)
    monkeypatch.setattr(graph_module, "result_formatter_node", fake_formatter)

    workflow = graph_module.build_graph().compile()
    result = workflow.invoke(AgentState(user_question="test"))

    assert result["generated_sql"] == "SELECT 1;"
    assert result["formatted_answer"]["summary"] == "ok"


def test_benchmark_intent_routes_without_orchestrator_llm(monkeypatch):
    from src.agents.orchestrator import orchestrator_node
    from src.agents.state import AgentState
    import src.agents.orchestrator as orchestrator_module

    def fail_invoke(*args, **kwargs):
        raise AssertionError("benchmark routing should not call the orchestrator LLM")

    monkeypatch.setattr(orchestrator_module, "invoke", fail_invoke)
    monkeypatch.setattr(orchestrator_module, "_retrieve_schema", lambda *args, **kwargs: "schema")

    state = AgentState(
        user_question="Doanh thu theo từng năm là bao nhiêu?",
        benchmark_context={"intent": "aggregate"},
        evaluation_options={"force_query_spec_for_all": False},
    )

    result = orchestrator_node(state)

    assert result.intent_type == "aggregate"
    assert result.next_agent == "query_spec"
    assert result.telemetry["orchestrator_decision"]["route_reason"] == "benchmark_contract"


def test_known_spider_question_does_not_exit_as_ambiguous(monkeypatch):
    import src.agents.orchestrator as orchestrator_module
    from src.agents.orchestrator import orchestrator_node

    monkeypatch.setattr(
        orchestrator_module,
        "invoke",
        lambda *args, **kwargs: (
            '{"intent_type":"ambiguous","confidence":0.9,'
            '"reasoning":"Cross-schema wording is uncertain."}'
        ),
    )
    monkeypatch.setattr(orchestrator_module, "_retrieve_schema", lambda *args, **kwargs: "schema")

    state = AgentState(
        user_question="Which airlines depart from both APG and CVO?",
        dataset_type="spider",
        benchmark_context={"db_id": "flight_2"},
        evaluation_options={"cache_enabled": False},
    )

    result = orchestrator_node(state)

    assert result.intent_type == "complex"
    assert result.next_agent == "query_spec"
    assert result.telemetry["orchestrator_decision"]["route_reason"] == "spec_required"


def test_spider_router_exception_uses_structured_fallback(monkeypatch):
    import src.agents.orchestrator as orchestrator_module
    from src.agents.orchestrator import orchestrator_node

    monkeypatch.setattr(
        orchestrator_module,
        "invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("truncated response")),
    )
    monkeypatch.setattr(orchestrator_module, "_retrieve_schema", lambda *args, **kwargs: "schema")

    state = AgentState(
        user_question="Which model has minimum horsepower?",
        dataset_type="spider",
        evaluation_options={"cache_enabled": False},
    )

    result = orchestrator_node(state)

    assert result.error is None
    assert result.next_agent == "query_spec"
    assert result.telemetry["orchestrator_diagnostic"]["error"] == "truncated response"


def test_run_telemetry_preserves_agent_diagnostics(monkeypatch):
    import asyncio
    import src.graph as graph_module
    from src.evaluation.telemetry import record_node_timing

    async def fake_run(**kwargs):
        record_node_timing("fake_node", 1.25)
        return AgentState(telemetry={"sql_generation_diagnostics": [{"stage": "extract"}]})

    monkeypatch.setattr(graph_module, "_arun_query_impl", fake_run)

    result = asyncio.run(graph_module.arun_query("test", session_id="telemetry_merge"))

    assert result.telemetry["sql_generation_diagnostics"] == [{"stage": "extract"}]
    assert result.telemetry["node_timings_ms"]["fake_node"] == [1.25]


def test_known_spider_question_can_skip_orchestrator_llm(monkeypatch):
    import src.agents.orchestrator as orchestrator_module
    from src.agents.orchestrator import orchestrator_node

    def fail_invoke(*args, **kwargs):
        raise AssertionError("known Spider SQL questions should bypass routing LLM")

    monkeypatch.setattr(orchestrator_module, "invoke", fail_invoke)
    monkeypatch.setattr(orchestrator_module, "_retrieve_schema", lambda *args, **kwargs: "schema")

    state = AgentState(
        user_question="Which city has the most departing flights?",
        dataset_type="spider",
        benchmark_context={"db_id": "flight_2", "known_sql_question": True},
        evaluation_options={
            "cache_enabled": False,
            "known_sql_direct_spec": True,
        },
    )

    result = orchestrator_node(state)

    assert result.next_agent == "query_spec"
    assert result.telemetry["orchestrator_decision"]["route_reason"] == "known_sql_direct_spec"


def test_validator_accepts_benchmark_limit_without_query_spec():
    from src.agents.validator import validator_node
    from src.agents.state import AgentState

    state = AgentState(
        generated_sql="SELECT OrderID FROM Orders ORDER BY OrderDate DESC LIMIT 10;",
        current_db_path="data/northwind/northwind.sqlite",
        query_spec={"output_columns": ["OrderID"], "limit": None},
        benchmark_context={
            "output_columns": ["OrderID"],
            "limit": 10,
        },
    )

    result = validator_node(state)

    assert result.next_agent == "executor"
    assert result.validation_report["errors"] == []
