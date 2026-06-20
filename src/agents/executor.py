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
        state.execution_error = 'No database file — cannot execute SQL'
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

        if (
            state.evaluation_options.get("self_correction_enabled", True)
            and state.generation_attempts < state.max_retries
        ):
            log.warning('sql_execution_error', error=result.error, sql=sql_to_exec[:80] if sql_to_exec else None, retry=True, attempt=state.generation_attempts)
            state.next_agent = 'sql_generator'
            state.retry_count += 1
            return state
        else:
            state.error = f"SQL execution failed: {result.error}\n(SQL: {sql_to_exec})"
            state.next_agent = 'error'
            return state
    else:
        # Zero-row self-correction logic (Content Matching)
        if (
            state.evaluation_options.get("self_correction_enabled", True)
            and result.row_count == 0
            and state.generation_attempts < state.max_retries
        ):
            # Check if SQL contains string equality (= 'val' or IN ('val'))
            if re.search(r"(?:=|IN\s*\()\s*['\"][^'\"]+['\"]", sql_to_exec, re.IGNORECASE):
                state.execution_error = "Execution successful, but returned 0 rows. Hint: The string in your WHERE clause might not perfectly match the database. Try using LIKE '%value%' (case-insensitive in SQLite) or check the spelling!"
                state.current_step = 'execution_failed_zero_rows'
                log.warning('sql_execution_zero_rows', sql=sql_to_exec[:80], retry=True, attempt=state.generation_attempts)
                state.next_agent = 'sql_generator'
                state.retry_count += 1
                return state

        state.query_result = {
            'sql': result.sql,
            'columns': result.columns,
            'rows': result.rows,
            'row_count': result.row_count,
            'execution_time_ms': result.execution_time_ms
        }
        state.execution_time_ms = result.execution_time_ms
        state.execution_error = None
        state.error = None
        state.current_step = 'execution_success'
        state.next_agent = 'formatter'
        
        if state.evaluation_options.get("cache_enabled", True):
            try:
                cache = get_semantic_cache()
                try:
                    cache.put(
                        state.user_question,
                        state.query_result,
                        state.generated_sql,
                        namespace=state.current_db_path,
                    )
                except TypeError:
                    cache.put(state.user_question, state.query_result, state.generated_sql)
                log.info("result_cached", sql=state.generated_sql[:60] if state.generated_sql else None)
            except Exception as cache_err:
                log.warning("cache_put_failed", error=str(cache_err))
        
        log.info('execution_success', row_count=result.row_count, time_ms=result.execution_time_ms)
        return state
