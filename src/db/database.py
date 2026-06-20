"""
Database Manager — khởi tạo, quản lý schema và thực thi SQL (Hỗ trợ SQLite, PostgreSQL, MySQL).
Sử dụng SQLAlchemy để trừu tượng hóa kết nối.
"""
import time
import pandas as pd
import structlog
from typing import Any, Optional
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from src.config import config
from src.schema import TableInfo, SchemaContext, QueryResult

log = structlog.get_logger("db")


class DatabaseManager:
    """Quản lý database đa nền tảng — schema introspection + query execution bằng SQLAlchemy."""

    def __init__(self, db_path: Optional[str] = None):
        path = (db_path or config.DB_PATH).strip()
        self.db_path = path

        # Xử lý URI (Backward Compatibility)
        if path.startswith("postgresql://") or path.startswith("postgresql+psycopg2://") or \
           path.startswith("mysql://") or path.startswith("mysql+pymysql://"):
            if path.startswith("mysql://"):
                path = path.replace("mysql://", "mysql+pymysql://", 1)
            # SQLAlchemy PyMySQL dialect does not support ssl-mode=REQUIRED query parameter
            if "mysql+pymysql" in path and "?ssl-mode" in path:
                path = path.split("?")[0]
            
            # Khắc phục lỗi nếu mật khẩu có chứa ký tự '@' dẫn đến '@@' trong URI (đặc biệt là Supabase)
            if "@@" in path:
                path = path.replace("@@", "%40@")
                
            self.db_url = path
        else:
            # Local SQLite file
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db_url = f"sqlite:///{path}"

        self.engine: Engine = create_engine(self.db_url, pool_pre_ping=True)

    # ── Schema Introspection ───────────────────────────────────────────────
    def get_schema(self) -> SchemaContext:
        """Đọc toàn bộ schema của database sử dụng SQLAlchemy (có Cache)."""
        if getattr(self, "_cached_schema", None) is not None:
            return self._cached_schema

        tables = self.get_all_tables_info()
        relationships = self._extract_relationships()
        self._cached_schema = SchemaContext(tables=tables, relationships=relationships)
        return self._cached_schema

    def get_all_tables_info(self) -> list[TableInfo]:
        """Lấy thông tin tất cả các bảng."""
        inspector = inspect(self.engine)
        table_names = inspector.get_table_names()

        tables = []
        for name in table_names:
            tables.append(self._get_table_info(inspector, name))
        return tables

    def _get_table_info(self, inspector, table_name: str) -> TableInfo:
        """Lấy thông tin chi tiết của một bảng."""
        columns_info = inspector.get_columns(table_name)
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_columns = pk_constraint.get("constrained_columns", []) if pk_constraint else []

        columns = []
        for col in columns_info:
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "pk": col["name"] in pk_columns,
            })

        # Lấy count và sample rows
        try:
            with self.engine.connect() as conn:
                # Dùng text() để bọc query
                # Count
                count_query = text(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
                row_count = conn.execute(count_query).scalar() or 0

                # Sample rows
                sample_query = text(f'SELECT * FROM "{table_name}" LIMIT 3')
                result = conn.execute(sample_query)
                
                rows = []
                for r in result.mappings():
                    d = dict(r)
                    for k, v in d.items():
                        if isinstance(v, bytes):
                            d[k] = "<binary>"
                    rows.append(d)
        except Exception as e:
            log.warning("error_getting_table_stats", table=table_name, error=str(e))
            row_count = 0
            rows = []

        return TableInfo(
            table_name=table_name,
            columns=columns,
            row_count=row_count,
            sample_rows=rows,
        )

    def _extract_relationships(self) -> list[dict]:
        """Trích xuất relationships giữa các bảng (foreign keys)."""
        inspector = inspect(self.engine)
        table_names = inspector.get_table_names()
        relationships = []

        for table_name in table_names:
            fks = inspector.get_foreign_keys(table_name)
            for fk in fks:
                if fk.get("constrained_columns") and fk.get("referred_columns"):
                    relationships.append({
                        "from_table": table_name,
                        "from_column": fk["constrained_columns"][0],
                        "to_table": fk["referred_table"],
                        "to_column": fk["referred_columns"][0],
                    })
        return relationships

    # ── Query Execution ───────────────────────────────────────────────────
    def execute_query(self, sql: str) -> QueryResult:
        """
        Thực thi SQL query và trả về kết quả.
        CHỈ cho phép SELECT queries (bảo mật).
        """
        start = time.perf_counter()
        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        # Security check — chỉ SELECT hoặc WITH (CTE)
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return QueryResult(
                sql=sql,
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=0,
                error="⚠️ Security: Only SELECT queries are allowed.",
            )

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql_stripped))
                
                columns = list(result.keys())
                rows = [dict(r) for r in result.mappings()]

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
                error=str(e).strip(),
            )

    # ── Table Management ──────────────────────────────────────────────────
    def create_table(self, table_name: str, columns: dict[str, str]):
        """Tạo bảng mới từ dictionary tên cột và kiểu dữ liệu."""
        col_defs = ", ".join([f'"{k}" {v}' for k, v in columns.items()])
        sql = f'CREATE TABLE "{table_name}" ({col_defs})'
        with self.engine.begin() as conn:
            conn.execute(text(sql))

    def insert_dataframe(self, df: pd.DataFrame, table_name: str, if_exists: str = "replace"):
        """Ghi DataFrame vào bảng (Dùng Pandas tự tương thích DB)."""
        with self.engine.begin() as conn:
            df.to_sql(table_name, conn, if_exists=if_exists, index=False)
        log.info("data_inserted", table=table_name, rows=len(df))

    def drop_table(self, table_name: str):
        """Xóa bảng."""
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))

    # ── Dynamic Validation Helpers ─────────────────────────────────────────
    def _get_inspector_cache(self):
        if getattr(self, "_cached_table_names", None) is None:
            inspector = inspect(self.engine)
            self._cached_table_names = inspector.get_table_names()
            self._cached_columns = {}
        return self._cached_table_names, self._cached_columns

    def table_exists(self, table_name: str) -> bool:
        """Kiểm tra bảng có tồn tại không (dùng Cache để giảm ping mạng)."""
        tables, _ = self._get_inspector_cache()
        return table_name.lower() in [t.lower() for t in tables]

    def get_table_columns(self, table_name: str) -> list[str]:
        """Lấy danh sách tên cột của một bảng (dùng Cache)."""
        tables, columns_cache = self._get_inspector_cache()
        actual_table = None
        for t in tables:
            if t.lower() == table_name.lower():
                actual_table = t
                break
        
        if not actual_table:
            return []
        
        if actual_table not in columns_cache:
            inspector = inspect(self.engine)
            cols = inspector.get_columns(actual_table)
            columns_cache[actual_table] = [c["name"] for c in cols]
            
        return columns_cache[actual_table]

    def column_exists(self, table_name: str, column_name: str) -> bool:
        """Kiểm tra cột có tồn tại trong bảng không."""
        cols = self.get_table_columns(table_name)
        return column_name.lower() in [c.lower() for c in cols]

    # ── Convenience ───────────────────────────────────────────────────────
    def execute_df(self, sql: str) -> pd.DataFrame:
        """Thực thi SQL và trả về DataFrame."""
        with self.engine.connect() as conn:
            return pd.read_sql_query(text(sql), conn)


# ── Evaluation Helpers ────────────────────────────────────────────────────

def run_sql(sql: str, db_path: str) -> list[Any]:
    """Thực thi SQL và trả về danh sách các dòng (dùng cho evaluation)."""
    db_url = db_path
    if not (db_path.startswith("postgresql") or db_path.startswith("mysql")):
        db_url = f"sqlite:///{db_path}"
    
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            return [dict(r) for r in result.mappings()]
    except Exception:
        return []


def execution_match(gold_sql: str, generated_sql: str, db_path: str) -> bool:
    """True nếu kết quả 2 SQL giống nhau."""
    gold_rows = run_sql(gold_sql, db_path)
    gen_rows = run_sql(generated_sql, db_path)
    return sorted(str(r) for r in gold_rows) == sorted(str(r) for r in gen_rows)


# Singleton instance
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[str] = None) -> DatabaseManager:
    """
    Get or create the global DatabaseManager singleton.
    """
    global _db_manager
    if db_path is not None:
        if _db_manager is None or _db_manager.db_path != db_path:
            _db_manager = DatabaseManager(db_path=db_path)
    elif _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
