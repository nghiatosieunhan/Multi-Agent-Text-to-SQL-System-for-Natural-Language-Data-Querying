"""
SQLite Database Manager — khởi tạo, quản lý schema và thực thi SQL.
"""
import sqlite3
import json
import time
from pathlib import Path
from typing import Any, Optional
from contextlib import contextmanager

import pandas as pd
import structlog
from src.config import config
from src.schema import TableInfo, SchemaContext, QueryResult, SQLQuery

log = structlog.get_logger("db")


class DatabaseManager:
    """Quản lý SQLite database — schema introspection + query execution."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # ── Connection ─────────────────────────────────────────────────────────
    @contextmanager
    def _get_conn(self):
        """Context manager cho SQLite connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ── Schema Introspection ───────────────────────────────────────────────
    def get_schema(self) -> SchemaContext:
        """Đọc toàn bộ schema của database."""
        with self._get_conn() as conn:
            tables = self.get_all_tables_info(conn)
            relationships = self._extract_relationships(conn)
        return SchemaContext(tables=tables, relationships=relationships)

    def get_all_tables_info(self, conn: sqlite3.Connection) -> list[TableInfo]:
        """Lấy thông tin tất cả các bảng."""
        cursor = conn.cursor()
        table_names = [r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]

        tables = []
        for name in table_names:
            tables.append(self._get_table_info(conn, name))
        return tables

    def _get_table_info(self, conn: sqlite3.Connection, table_name: str) -> TableInfo:
        """Lấy thông tin chi tiết của một bảng."""
        cursor = conn.cursor()

        # Columns
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = [
            {
                "name": r["name"],
                "type": r["type"],
                "nullable": not bool(r["notnull"]),
                "pk": bool(r["pk"]),
            }
            for r in cursor.fetchall()
        ]

        # Row count
        cursor.execute(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
        row_count = cursor.fetchone()["cnt"]

        # Sample rows (limit 3)
        cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 3')
        rows = []
        for r in cursor.fetchall():
            d = dict(r)
            for k, v in d.items():
                if isinstance(v, bytes):
                    d[k] = "<binary>"
            rows.append(d)

        return TableInfo(
            table_name=table_name,
            columns=columns,
            row_count=row_count,
            sample_rows=rows,
        )

    def _extract_relationships(self, conn: sqlite3.Connection) -> list[dict]:
        """Cố gắng trích xuất relationships giữa các bảng (foreign keys)."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        relationships = []
        for (table_name,) in cursor.fetchall():
            cursor.execute(f'PRAGMA foreign_key_list("{table_name}")')
            for fk in cursor.fetchall():
                relationships.append({
                    "from_table": table_name,
                    "from_column": fk["from"],
                    "to_table": fk["table"],
                    "to_column": fk["to"],
                })
        return relationships

    def get_table_ddl(self, table_name: str) -> str:
        """Lấy DDL của một bảng."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                (table_name,)
            )
            result = cursor.fetchone()
            return result["sql"] if result else ""

    # ── Query Execution ───────────────────────────────────────────────────
    def execute_query(self, sql: str) -> QueryResult:
        """
        Thực thi SQL query và trả về kết quả.
        CHỈ cho phép SELECT queries (bảo mật).
        """
        start = time.perf_counter()
        sql_stripped = sql.strip().upper()

        # Security check — chỉ SELECT hoặc WITH (CTE)
        if not (sql_stripped.startswith("SELECT") or sql_stripped.startswith("WITH")):
            return QueryResult(
                sql=sql,
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=0,
                error="⚠️ Security: Only SELECT queries are allowed.",
            )

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(sql)
                rows = [dict(r) for r in cursor.fetchall()]
                columns = [desc[0] for desc in cursor.description] if cursor.description else []

            elapsed = (time.perf_counter() - start) * 1000
            return QueryResult(
                sql=sql,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=round(elapsed, 2),
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return QueryResult(
                sql=sql,
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=round(elapsed, 2),
                error=str(e),
            )

    # ── Table Management ──────────────────────────────────────────────────
    def create_table(self, table_name: str, columns: dict[str, str]):
        """Tạo bảng mới từ dict {col_name: type}."""
        cols_sql = ", ".join(f"{k} {v}" for k, v in columns.items())
        with self._get_conn() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_sql})")
            conn.commit()
        log.info("table_created", table=table_name, columns=list(columns.keys()))

    def insert_dataframe(self, df: pd.DataFrame, table_name: str, if_exists: str = "replace"):
        """Ghi DataFrame vào bảng SQLite."""
        with self._get_conn() as conn:
            df.to_sql(table_name, conn, if_exists=if_exists, index=False)
            conn.commit()
        log.info("data_inserted", table=table_name, rows=len(df))

    def drop_table(self, table_name: str):
        """Xóa bảng (chỉ dùng trong setup/testing)."""
        with self._get_conn() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()

    # ── Dynamic Validation Helpers ─────────────────────────────────────────
    def table_exists(self, table_name: str) -> bool:
        """Kiểm tra bảng có tồn tại không (case-insensitive)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            result = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND LOWER(name)=LOWER(?)",
                (table_name,)
            ).fetchone()
            return result is not None

    def get_table_columns(self, table_name: str) -> list[str]:
        """Lấy danh sách tên cột của một bảng (case-insensitive table name)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            # Find actual table name (case-insensitive) using safe query
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)=LOWER(?)",
                (table_name,)
            )
            row = cur.fetchone()
            if not row:
                return []
            # Use quoted identifier for safety
            actual_name = row[0]
            quoted = f'"{actual_name}"'
            cur.execute(f"PRAGMA table_info({quoted})")
            return [r[1] for r in cur.fetchall()]

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Kiểm tra cột có tồn tại trong bảng không."""
        cols = self.get_table_columns(table_name)
        return column_name in cols

    # ── Convenience ───────────────────────────────────────────────────────
    def execute_df(self, sql: str) -> pd.DataFrame:
        """Thực thi SQL và trả về DataFrame."""
        with self._get_conn() as conn:
            return pd.read_sql_query(sql, conn)


# ── Evaluation Helpers ────────────────────────────────────────────────────

def run_sql(sql: str, db_path: str) -> list[Any]:
    """Thực thi SQL và trả về danh sách các dòng (dùng cho evaluation)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    finally:
        conn.close()


def execution_match(gold_sql: str, generated_sql: str, db_path: str) -> bool:
    """True nếu kết quả 2 SQL giống nhau (không phân biệt thứ tự dòng)."""
    gold_rows = run_sql(gold_sql, db_path)
    gen_rows = run_sql(generated_sql, db_path)
    return sorted(str(r) for r in gold_rows) == sorted(str(r) for r in gen_rows)


# Singleton instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[str] = None) -> DatabaseManager:
    """
    Get or create the global DatabaseManager singleton.
    Pass db_path to override the default path.
    NOTE: If db_path differs from the current singleton's path,
    a new DatabaseManager is created for that path.
    """
    global _db_manager
    if db_path is not None:
        # Always use the requested path — create new instance if path changed
        if _db_manager is None or _db_manager.db_path != db_path:
            _db_manager = DatabaseManager(db_path=db_path)
    elif _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
