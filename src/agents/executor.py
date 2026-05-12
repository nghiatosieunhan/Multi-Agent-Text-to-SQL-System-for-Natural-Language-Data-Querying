import re
import structlog
from src.agents.state import AgentState
from src.db import get_db_manager
from src.memory import get_semantic_cache

log = structlog.get_logger("executor")

def _fix_common_errors(sql: str) -> str:
    sql = re.sub(r'\bSELECT\s+TOP\s+(\d+)\b', r'SELECT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bLIMIT\s+(\d+)(st|nd|rd|th)\b', r'LIMIT \1', sql, flags=re.IGNORECASE)
    sql = sql.replace("\\'", "''")
    return sql

def executor_node(state: AgentState) -> dict:
    if not state.current_db_path:
        state.execution_error = 'No database file — cannot execute SQL (Spider evaluation with schema-only)'
        state.current_step = 'execution_skipped'
        state.next_agent = 'result_formatter'
        return state

    log.info("executor_run", sql=state.generated_sql[:80] if state.generated_sql else None)

    if not state.generated_sql:
        state.error = 'No SQL to execute — generator failed.'
        state.next_agent = 'error'
        return state

    db = get_db_manager(state.current_db_path) if state.current_db_path else None
    sql_to_exec = _fix_common_errors(state.generated_sql)

    result = db.execute_query(sql_to_exec)

    if result.error:
        state.execution_error = result.error
        state.execution_time_ms = result.execution_time_ms
        state.current_step = 'execution_failed'

        if state.generation_attempts < state.max_retries:
            log.warning('sql_execution_error', error=result.error, sql=sql_to_exec[:80] if sql_to_exec else None, retry=True, attempt=state.generation_attempts)
            state.next_agent = 'sql_generator'
            state.retry_count += 1
            return state
        else:
            state.error = f"SQL execution failed after {state.max_retries} retries: {result.error}\n(SQL: {sql_to_exec})"
            state.next_agent = 'error'
            return state
    else:
        state.query_result = {
            'sql': result.sql,
            'columns': result.columns,
            'rows': result.rows,
            'row_count': result.row_count,
            'execution_time_ms': result.execution_time_ms
        }
        state.execution_time_ms = result.execution_time_ms
        state.current_step = 'execution_success'
        state.next_agent = 'formatter'
        
        try:
            cache = get_semantic_cache()
            cache.put(state.user_question, state.query_result, state.generated_sql)
            log.info("result_cached", sql=state.generated_sql[:60] if state.generated_sql else None)
        except Exception as cache_err:
            log.warning("cache_put_failed", error=str(cache_err))
        
        log.info('execution_success', row_count=result.row_count, time_ms=result.execution_time_ms)
        return state
