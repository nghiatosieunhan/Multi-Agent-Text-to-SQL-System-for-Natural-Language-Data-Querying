"""
Pytest tests cho Multi-Agent Text-to-SQL System.
"""
import pytest
import pandas as pd

from src.db import DatabaseManager
from src.db.data_pipeline import DataPipeline, DataCleaner, DataTransformer
from src.memory.semantic_cache import SemanticCache
from src.tools.visualizer import render_table_ascii, suggest_chart_type


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    """In-memory database cho testing."""
    db_path = str(tmp_path / "test.db")
    return DatabaseManager(db_path=db_path)


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "Product Name": ["A", "B", "C"],
        "Price  (VND)": [100, 200, 150],
        "Quantity in Stock": [10, 0, 5],
    })


# ── Data Pipeline Tests ───────────────────────────────────────────────────────
class TestDataCleaner:
    def test_clean_column_names(self, sample_df):
        cleaner = DataCleaner()
        df = cleaner.clean_column_names(sample_df)
        assert all(c.isidentifier() or c.replace("_", "").isalnum() for c in df.columns)
        assert "price__vnd" in df.columns or "price_vnd" in df.columns

    def test_infer_sqlite_types(self, sample_df):
        cleaner = DataCleaner()
        types = cleaner.infer_sqlite_types(sample_df)
        # All columns should have valid SQLite types
        assert all(v in ("INTEGER", "REAL", "TEXT") for v in types.values())
        assert len(types) == len(sample_df.columns)


class TestDataTransformer:
    def test_anonymize(self, sample_df):
        transformer = DataTransformer()
        df = transformer.anonymize(sample_df, sensitive_cols=["Product Name"])
        assert list(df["Product Name"]) != list(sample_df["Product Name"])
        # Hash length = 12
        assert len(str(df["Product Name"].iloc[0])) == 12


class TestDataPipeline:
    def test_ingest_sample_data(self, db):
        pipeline = DataPipeline(db)
        pipeline.ingest_sample_data()

        schema = db.get_schema()
        table_names = [t.table_name for t in schema.tables]

        assert "products" in table_names
        assert "customers" in table_names
        assert "orders" in table_names
        assert "suppliers" in table_names
        assert "reviews" in table_names

        # Check row counts
        products = next(t for t in schema.tables if t.table_name == "products")
        assert products.row_count == 20


# ── Semantic Cache Tests ──────────────────────────────────────────────────────
class TestSemanticCache:
    def test_cache_put_get(self):
        cache = SemanticCache(max_size=10)
        result = {"rows": [{"a": 1}], "row_count": 1}
        cache.put("Tổng số sản phẩm?", result, "SELECT COUNT(*) FROM products")
        cached = cache.get("Tổng số sản phẩm?")
        assert cached is not None
        assert cached["row_count"] == 1

    def test_cache_miss(self):
        cache = SemanticCache(max_size=10)
        cached = cache.get("Câu hỏi hoàn toàn khác???")
        assert cached is None

    def test_cache_eviction(self):
        cache = SemanticCache(max_size=3)
        for i in range(5):
            cache.put(f"Question {i}?", {"row_count": i}, "SELECT {i}")
        stats = cache.stats()
        assert stats["size"] == 3

    def test_cache_stats(self):
        cache = SemanticCache(max_size=5)
        # Use EXACT same question for hit — guaranteed similarity 1.0
        q = "Tổng số sản phẩm theo danh mục là gì?"
        cache.put(q, {"r": 1}, "SQL1")
        cache.get(q)  # exact match → hit
        cache.get("Câu hỏi hoàn toàn khác về doanh thu tháng 6 năm 2024")  # miss
        s = cache.stats()
        assert s["hits"] >= 1   # at least one hit (exact match)
        assert s["misses"] >= 1  # at least one miss
        assert s["hit_rate"] >= 0  # valid rate


# ── SQL Execution Tests ──────────────────────────────────────────────────────
class TestDatabase:
    def test_execute_select(self, db):
        db.create_table("test", {"id": "INTEGER", "name": "TEXT"})
        db.insert_dataframe(pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}), "test")

        result = db.execute_query("SELECT * FROM test")
        assert result.error is None
        assert result.row_count == 2
        assert result.columns == ["id", "name"]

    def test_security_block_dangerous(self, db):
        result = db.execute_query("DROP TABLE test")
        assert result.error is not None
        assert "Security" in result.error

    def test_error_handling(self, db):
        result = db.execute_query("SELECT * FROM nonexistent")
        assert result.error is not None


# ── Visualizer Tests ─────────────────────────────────────────────────────────
class TestVisualizer:
    def test_render_table(self):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        table = render_table_ascii(["a", "b"], rows)
        assert "a" in table
        assert "b" in table
        assert "1" in table

    def test_suggest_chart(self):
        rows = [{"name": "A", "value": 10}, {"name": "B", "value": 20}]
        chart = suggest_chart_type(["name", "value"], rows)
        assert chart in ("bar", "table")


# ── Agent Prompts Tests ───────────────────────────────────────────────────────
class TestAgents:
    def test_sql_safety_validation(self):
        """Test SQL generator safety check."""
        from src.agents.sql_generator import _validate_sql_safety
        safe_sql = "SELECT * FROM products WHERE price > 100"
        is_safe, issues = _validate_sql_safety(safe_sql)
        assert is_safe
        assert len(issues) == 0

    def test_dangerous_sql_blocked(self):
        from src.agents.sql_generator import _validate_sql_safety
        dangerous = "SELECT * FROM products; DROP TABLE products;"
        is_safe, issues = _validate_sql_safety(dangerous)
        assert not is_safe
        assert any("DROP" in i for i in issues)
