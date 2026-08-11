"""
Pydantic schemas cho messages và state trong LangGraph.
"""
from typing import Optional, Any
from pydantic import BaseModel, Field


# ── Query / SQL Schemas ────────────────────────────────────────────────────
class SQLQuery(BaseModel):
    """SQL query được sinh ra bởi generator agent."""
    sql: str = Field(description="Câu lệnh SQL hoàn chỉnh, chỉ có SELECT (không có DROP/INSERT/UPDATE/DELETE)")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Độ tự tin của model (0-1)")
    reasoning: Optional[str] = Field(default=None, description="Giải thích cách query được xây dựng")
    tables_used: list[str] = Field(default_factory=list, description="Danh sách bảng được sử dụng")
    columns_used: list[str] = Field(default_factory=list, description="Danh sách cột được sử dụng")
    query_type: str = Field(default="SELECT", description="Loại query: SELECT, AGGREGATE, JOIN, etc.")


class QueryResult(BaseModel):
    """Kết quả sau khi thực thi SQL."""
    sql: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    execution_time_ms: float
    error: Optional[str] = None
    from_cache: bool = False


# ── RAG / Knowledge Base ───────────────────────────────────────────────────
class TableInfo(BaseModel):
    """Metadata về một bảng trong database."""
    table_name: str
    columns: list[dict]          # [{'name': str, 'type': str, 'nullable': bool, 'pk': bool}]
    description: Optional[str] = None
    row_count: Optional[int] = None
    sample_rows: list[dict] = Field(default_factory=list, max_length=3)


class SchemaContext(BaseModel):
    """Context về schema database — dùng để RAG."""
    tables: list[TableInfo]
    relationships: list[dict] = Field(default_factory=list)


# ── Semantic Cache ─────────────────────────────────────────────────────────
class CachedResult(BaseModel):
    """Kết quả được cache lại."""
    sql: str
    result: dict          # serialized QueryResult
    semantic_key: str      # hash của câu hỏi gốc
    created_at: float


# ── Agent Messages ─────────────────────────────────────────────────────────
class AgentMessage(BaseModel):
    """Message chuẩn hóa giữa các agents."""
    sender: str
    receiver: str
    content: Any
    metadata: dict = Field(default_factory=dict)


# ── Visualization ──────────────────────────────────────────────────────────
class VisualizationSpec(BaseModel):
    """Spec cho chart/visualization."""
    chart_type: str           # 'bar', 'line', 'pie', 'table'
    title: str
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    data_description: str
