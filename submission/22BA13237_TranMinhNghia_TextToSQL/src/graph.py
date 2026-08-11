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
from src.agents.query_spec import query_spec_node
from src.agents.sql_generator import sql_generator_node
from src.agents.validator import validator_node
from src.agents.executor import executor_node
from src.agents.result_formatter import result_formatter_node
from src.config import config
from src.evaluation.telemetry import timed_node, telemetry_run

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

    graph.add_node("router", timed_node("router", router_node))
    graph.add_node("orchestrator", timed_node("orchestrator", orchestrator_node))
    graph.add_node("query_planner", timed_node("query_planner", query_planner_node))
    graph.add_node("query_spec", timed_node("query_spec", query_spec_node))
    graph.add_node("sql_generator", timed_node("sql_generator", sql_generator_node))
    graph.add_node("validator", timed_node("validator", validator_node))
    graph.add_node("executor", timed_node("executor", executor_node))
    graph.add_node("result_formatter", timed_node("result_formatter", result_formatter_node))

    # 1. Khởi động từ Router
    # (Router route will handle whether to go to orchestrator or sql_generator)
    # Wait, the user suggested START -> input_adapter -> router.
    graph.add_node("input_adapter", timed_node("input_adapter", input_adapter_node))
    graph.add_node("output_adapter", timed_node("output_adapter", output_adapter_node))

    graph.set_entry_point("input_adapter")
    graph.add_edge("input_adapter", "router")
    
    # 2. Router route
    def router_route(state: AgentState) -> str:
        if state.error:
            return END
        if state.next_agent == "result_formatter":
            return "result_formatter"
        if state.next_agent == "sql_generator":
            return "sql_generator"
        return "orchestrator"

    graph.add_conditional_edges(
        source="router",
        path=router_route,
        path_map={
            "orchestrator": "orchestrator",
            "result_formatter": "result_formatter",
            "sql_generator": "sql_generator",
            END: END,
        },
    )
    # 3. Orchestrator route
    def orchestrator_route(state: AgentState) -> str:
        if state.error:
            return END
        if state.cache_hit:
            return "result_formatter"
        if state.next_agent == "query_spec":
            return "query_spec"
        if state.plan_needed:
            return "query_planner"
        return "sql_generator"

    graph.add_conditional_edges(
        source="orchestrator",
        path=orchestrator_route,
        path_map={
            "result_formatter": "result_formatter",
            "sql_generator": "sql_generator",
            "query_planner": "query_planner",
            "query_spec": "query_spec",
            END: END,
        },
    )

    # 4. Query Spec → SQL Generator (always, spec is just context)
    graph.add_edge("query_spec", "sql_generator")
    graph.add_edge("query_planner", "sql_generator")

    # 5. Query Planner → SQL Generator

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

    # 8. Result Formatter → output_adapter → END
    graph.add_edge("result_formatter", "output_adapter")
    graph.add_edge("output_adapter", END)

    return graph


_workflow = None

def input_adapter_node(state: AgentState):
    from langchain_core.messages import HumanMessage
    import re
    
    # 1. Get raw question
    if state.messages:
        raw_question = state.messages[-1].content
        ret = {"user_question": raw_question}
    else:
        raw_question = state.user_question
        ret = {"messages": [HumanMessage(content=raw_question)]}
        
    # 2. Extract Entities via Regex
    entities = {}
    if ip_match := re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', raw_question):
        entities["ip"] = ip_match.group(0)
    if emp_match := re.search(r'NV-[0-9]+', raw_question, re.IGNORECASE):
        entities["employee_id"] = emp_match.group(0).upper()
        
    # 3. Check Fast Route
    fast_route_patterns = [r"^danh sách sản phẩm$", r"^doanh thu hôm nay$"]
    is_fast = any(re.match(p, raw_question.strip().lower()) for p in fast_route_patterns)
    
    # 4. Dialect detection
    dialect = "sqlite"
    if state.current_db_path.startswith("postgresql"):
        dialect = "postgresql"
    elif state.current_db_path.startswith("mysql"):
        dialect = "mysql"
    ret["db_dialect"] = dialect

    # 5. Update state with Regex results and override next_agent if fast route
    ret["extracted_entities"] = entities
    ret["is_fast_route"] = is_fast
    if is_fast:
        ret["next_agent"] = "sql_generator"
        
    return ret

def output_adapter_node(state: AgentState):
    from langchain_core.messages import AIMessage
    
    # Lấy câu trả lời cuối cùng từ formatted_answer
    content = ""
    if state.formatted_answer:
        if "chat_response" in state.formatted_answer:
            content = state.formatted_answer["chat_response"]
        elif "detailed_answer" in state.formatted_answer:
            content = state.formatted_answer["detailed_answer"]
        elif "summary" in state.formatted_answer:
            content = state.formatted_answer["summary"]
            
    return {"messages": [AIMessage(content=content)]}

_memory = None

def get_workflow():
    global _workflow, _memory
    if _workflow is None:
        from langgraph.checkpoint.memory import MemorySaver
        _memory = MemorySaver()
        graph = build_graph()
        _workflow = graph.compile(checkpointer=_memory)
    return _workflow


async def _arun_query_impl(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
    evidence: str = None,
    analysis_mode: str = "deep",
    evaluation_profile: str = "full",
    evaluation_options: dict = None,
    benchmark_context: dict = None,
) -> AgentState:
    # Resolve và init DB (rebuild schema nếu đổi DB)
    # Khi db_path="" + có override_schema_context → Spider evaluation, không init DB
    if db_path or override_schema_context is None:
        resolved_db_path = _ensure_db(db_path)
    else:
        resolved_db_path = ""

    log.info("arun_query_start", question=question[:100], session_id=session_id, db=resolved_db_path)

    # Detect db_dialect from path
    db_dialect = "sqlite"
    if resolved_db_path:
        if resolved_db_path.startswith("postgresql"):
            db_dialect = "postgresql"
        elif resolved_db_path.startswith("mysql"):
            db_dialect = "mysql"

    initial_state = AgentState(
        user_question=question,
        session_id=session_id,
        current_step="start",
        current_db_path=resolved_db_path,
        override_schema_context=override_schema_context,
        dataset_type=dataset_type,
        evidence=evidence or "",
        analysis_mode=analysis_mode,
        db_dialect=db_dialect,
        evaluation_profile=evaluation_profile,
        evaluation_options=evaluation_options or {},
        benchmark_context=benchmark_context or {},
    )

    workflow = get_workflow()
    runtime_config = {"configurable": {"thread_id": session_id}, "recursion_limit": 25}
    raw_result = await workflow.ainvoke(initial_state, config=runtime_config)
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


async def arun_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
    evidence: str = None,
    analysis_mode: str = "deep",
    evaluation_profile: str = "full",
    evaluation_options: dict = None,
    benchmark_context: dict = None,
) -> AgentState:
    """Run one query and attach isolated evaluation telemetry."""
    from copy import deepcopy
    from src.evaluation.profiles import get_profile_options

    options = get_profile_options(evaluation_profile, evaluation_options)
    if options.get("baseline"):
        from src.evaluation.baselines import arun_baseline_query

        return await arun_baseline_query(
            question=question,
            session_id=session_id,
            db_path=db_path,
            baseline=options["baseline"],
            dataset_type=dataset_type,
            benchmark_context=benchmark_context,
        )

    with telemetry_run(session_id) as collector:
        result = await _arun_query_impl(
            question=question,
            session_id=session_id,
            db_path=db_path,
            override_schema_context=override_schema_context,
            dataset_type=dataset_type,
            evidence=evidence,
            analysis_mode=analysis_mode,
            evaluation_profile=evaluation_profile,
            evaluation_options=options,
            benchmark_context=benchmark_context,
        )
    # Preserve agent-level diagnostics/decisions while attaching authoritative
    # token and timing counters from the isolated collector.
    agent_telemetry = deepcopy(result.telemetry or {})
    result.telemetry = {**agent_telemetry, **deepcopy(collector)}
    return result


def run_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
    evidence: str = None,
    analysis_mode: str = "deep",
    evaluation_profile: str = "full",
    evaluation_options: dict = None,
    benchmark_context: dict = None,
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
                    asyncio.run,
                    arun_query(
                        question,
                        session_id,
                        db_path,
                        override_schema_context,
                        dataset_type,
                        evidence,
                        analysis_mode,
                        evaluation_profile,
                        evaluation_options,
                        benchmark_context,
                    ),
                )
                return future.result()
        else:
            return asyncio.run(arun_query(
                question,
                session_id,
                db_path,
                override_schema_context,
                dataset_type,
                evidence,
                analysis_mode,
                evaluation_profile,
                evaluation_options,
                benchmark_context,
            ))
    except RuntimeError:
        return asyncio.run(arun_query(
            question,
            session_id,
            db_path,
            override_schema_context,
            dataset_type,
            evidence,
            analysis_mode,
            evaluation_profile,
            evaluation_options,
            benchmark_context,
        ))

def stream_query(
    question: str,
    session_id: str = "default",
    db_path: str = "",
    override_schema_context: str = None,
    dataset_type: str = None,
    analysis_mode: str = "deep",
):
    if db_path or override_schema_context is None:
        resolved_db_path = _ensure_db(db_path)
    else:
        resolved_db_path = ""

    # Detect db_dialect from path
    db_dialect = "sqlite"
    if resolved_db_path:
        if resolved_db_path.startswith("postgresql"):
            db_dialect = "postgresql"
        elif resolved_db_path.startswith("mysql"):
            db_dialect = "mysql"

    initial_state = AgentState(
        user_question=question,
        session_id=session_id,
        current_step="start",
        current_db_path=resolved_db_path,
        override_schema_context=override_schema_context,
        dataset_type=dataset_type,
        analysis_mode=analysis_mode,
        db_dialect=db_dialect,
    )

    workflow = get_workflow()
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 25}
    for output in workflow.stream(initial_state, config=config):
        yield output
