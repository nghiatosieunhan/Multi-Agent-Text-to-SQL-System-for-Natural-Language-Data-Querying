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

    prompt = SYSTEM_PROMPT_TEMPLATE.format(schema="TABLE Products", db_dialect="sqlite")

    assert "SELECT only the columns or aggregates explicitly requested" in prompt
    assert "SELECT BOTH Primary Key ID" not in prompt


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
