"""
LangGraph Workflow — Multi-Agent Text-to-SQL Pipeline.
Định nghĩa graph, edges, và entry point.
Hỗ trợ multi-DB: mỗi query có thể chạy trên SQLite file khác nhau.
"""
import structlog
from langgraph.graph import StateGraph, END
from src.agents.route_node import router_node
from src.agents.state import AgentState
from src.agents.orchestrator import orchestrator_node
from src.agents.query_planner import query_planner_node
from src.agents.sql_generator import sql_generator_node
from src.agents.validator import validator_node
from src.agents.executor import executor_node
from src.agents.result_formatter import result_formatter_node
from src.config import config

log = structlog.get_logger("langgraph")

# ── Current DB path (global — set when DB changes) ───────────────────────────
_current_db_path: str = ""


def _ensure_db(db_path: str) -> str:
    """
    Ensure DB manager và schema index được init/reinit khi đổi DB.
    Trả về resolved db_path.
    """
    global _current_db_path

    resolved = db_path or config.DB_PATH

    # Nếu đổi DB → rebuild schema index với semantic descriptions
    if resolved != _current_db_path:
        log.info("db_changed", old=_current_db_path or "(none)", new=resolved)

        # Init/reinit DB manager với DB mới
        from src.db import get_db_manager
        get_db_manager(resolved)

        # Rebuild schema index với semantic descriptions
        from src.agents.onboard import get_current_db_schema
        from src.rag.schema_indexer import rebuild_schema_index
        from src.db import get_db_manager as _get_db

        try:
            schema, _ = get_current_db_schema(resolved)
            rebuild_schema_index(_get_db(resolved), db_path=resolved)
            log.info("schema_index_rebuilt", db_path=resolved, tables=len(schema.tables))
        except Exception as e:
            log.warning("schema_rebuild_failed", error=str(e))

        _current_db_path = resolved

    return resolved


# ── Graph Definition ─────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Pipeline:
    orchestrator
        ↓
    [plan_needed?] → query_planner   (join/aggregate/complex/cte/subquery)
        ↓ no
    sql_generator
        ↓
    validator          ← hard-validate SQL (dynamic table/column checks)
        ↓ valid
    executor
        ↓
    result_formatter → END

    Nếu validator fail → retry sql_generator (tối đa max_retries lần)
    """
    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("query_planner", query_planner_node)
    graph.add_node("sql_generator", sql_generator_node)
    graph.add_node("validator", validator_node)
    graph.add_node("executor", executor_node)
    graph.add_node("result_formatter", result_formatter_node)

    # 1. Khởi động từ Router
    graph.set_entry_point("router")
    
    # 2. Router route
    def router_route(state: AgentState) -> str:
        if state.error:
            return END
        if state.next_agent == "sql_generator":
            return "sql_generator"
        return "orchestrator"

    graph.add_conditional_edges(
        source="router",
        path=router_route,
        path_map={
            "orchestrator": "orchestrator",
            "sql_generator": "sql_generator",
            END: END,
        },
    )
    # 3. Orchestrator route
    def orchestrator_route(state: AgentState) -> str:
        if state.error:
            return END
        if state.plan_needed:
            return "query_planner"
        return "sql_generator"

    graph.add_conditional_edges(
        source="orchestrator",
        path=orchestrator_route,
        path_map={
            "sql_generator": "sql_generator",
            "query_planner": "query_planner",
        },
    )

    # 4. Query Planner → SQL Generator
    graph.add_edge("query_planner", "sql_generator")

    # 5. SQL Generator → Validator
    graph.add_edge("sql_generator", "validator")

    # 6. Validator route
    def validator_route(state: AgentState) -> str:
        if state.error:
            return END
        if state.next_agent == "sql_generator":
            return "sql_generator"
        if state.next_agent == "executor":
            return "executor"
        return END

    graph.add_conditional_edges(
        source="validator",
        path=validator_route,
        path_map={
            "sql_generator": "sql_generator",
            "executor": "executor",
            END: END,
        },
    )

    # 7. Executor route
    def executor_route(state: AgentState) -> str:
        if state.error and "retry" not in state.next_agent:
            return END
        if state.next_agent == "error":
            return END
        if state.next_agent == "sql_generator" and state.retry_count < state.max_retries:
            log.info("executor_retry", attempt=state.retry_count + 1)
            return "sql_generator"
        return "result_formatter"

    graph.add_conditional_edges(
        source="executor",
        path=executor_route,
        path_map={
            "sql_generator": "sql_generator",
            "result_formatter": "result_formatter",
            END: END,
        },
    )

    # 8. Result Formatter → END
    graph.add_edge("result_formatter", END)

    return graph


_workflow = None


def get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = build_graph().compile()
    return _workflow


async def arun_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
) -> AgentState:
    # Resolve và init DB (rebuild schema nếu đổi DB)
    # Khi db_path="" + có override_schema_context → Spider evaluation, không init DB
    if db_path or override_schema_context is None:
        resolved_db_path = _ensure_db(db_path)
    else:
        resolved_db_path = ""

    log.info("arun_query_start", question=question[:100], session_id=session_id, db=resolved_db_path)

    initial_state = AgentState(
        user_question=question,
        session_id=session_id,
        current_step="start",
        current_db_path=resolved_db_path,
        override_schema_context=override_schema_context,
        dataset_type=dataset_type,
    )

    workflow = get_workflow()
    raw_result = await workflow.ainvoke(initial_state)
    if isinstance(raw_result, dict):
        result = AgentState(**raw_result)
    else:
        result = raw_result

    log.info(
        "arun_query_complete",
        question=question[:60],
        step=result.current_step,
        cache_hit=result.cache_hit,
        sql=result.generated_sql[:60] if result.generated_sql else None,
    )
    return result


def run_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
) -> AgentState:
    """
    Run a single query against the specified SQLite database.

    Args:
        question: User's natural language question
        session_id: Session ID for tracking
        db_path: Path to SQLite file. Uses config.DB_PATH if empty.
                 Triggers schema rebuild + semantic onboarding khi đổi DB.
        override_schema_context: Override schema context — dùng cho Spider evaluation
                                 (parse từ dataset input, không cần SQLite file).
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run, arun_query(question, session_id, db_path, override_schema_context, dataset_type)
                )
                return future.result()
        else:
            return asyncio.run(arun_query(question, session_id, db_path, override_schema_context, dataset_type))
    except RuntimeError:
        return asyncio.run(arun_query(question, session_id, db_path, override_schema_context))

def stream_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
):
    if db_path or override_schema_context is None:
        resolved_db_path = _ensure_db(db_path)
    else:
        resolved_db_path = ""

    initial_state = AgentState(
        user_question=question,
        session_id=session_id,
        current_step="start",
        current_db_path=resolved_db_path,
        override_schema_context=override_schema_context,
        dataset_type=dataset_type,
    )

    workflow = get_workflow()
    for output in workflow.stream(initial_state):
        yield output
