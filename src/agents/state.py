"""
LangGraph State — định nghĩa state schema cho multi-agent graph.
"""
from typing import Optional, Any
from pydantic import BaseModel, Field
from langgraph.graph import add_messages
from src.schema import SchemaContext


class AgentState(BaseModel):
    """
    Shared state giữa tất cả agents trong LangGraph.
    Các field được cập nhật xuyên suốt pipeline.
    """
    # ── Input ─────────────────────────────────────────────────────────────
    user_question: str = Field(default="", description="Câu hỏi gốc từ user")
    session_id: str = Field(default="default", description="Session ID cho tracking")

    # ── Multi-DB ──────────────────────────────────────────────────────────
    current_db_path: str = Field(default="", description="Đường dẫn SQLite file hiện tại")
    current_db_schema: Optional[SchemaContext] = Field(
        default=None, description="Schema context đã onboarded cho DB hiện tại"
    )

    # ── Orchestrator ──────────────────────────────────────────────────────
    intent_type: str = Field(default="unknown", description="Loại intent: simple|aggregate|join|complex|ambiguous")
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    orchestrator_reasoning: str = Field(default="")

    # ── Cache ─────────────────────────────────────────────────────────────
    cache_checked: bool = Field(default=False)
    cache_hit: bool = Field(default=False)
    cached_result: Optional[dict] = Field(default=None, description="Kết quả từ cache")

    # ── Schema / RAG ──────────────────────────────────────────────────────
    schema_context: str = Field(default="", description="Schema context từ RAG retrieval")
    override_schema_context: Optional[str] = Field(
        default=None,
        description="Override schema — dùng thay thế RAG retrieval (Spider evaluation)"
    )
    tables_identified: list[str] = Field(default_factory=list)
    columns_identified: list[str] = Field(default_factory=list)

    # ── Query Planner ─────────────────────────────────────────────────────
    plan: Optional[dict] = Field(default=None, description="Execution plan từ QueryPlanner")
    plan_needed: bool = Field(default=False, description="Có cần plan phức tạp không")

    # ── SQL Generator ─────────────────────────────────────────────────────
    generated_sql: str = Field(default="", description="SQL query được sinh ra")
    sql_confidence: float = Field(default=0.0)
    sql_reasoning: str = Field(default="")
    sql_validation: Optional[dict] = Field(default=None)
    generation_attempts: int = Field(default=0)

    # ── Executor ─────────────────────────────────────────────────────────
    query_result: Optional[dict] = Field(default=None, description="Kết quả thực thi SQL")
    execution_error: Optional[str] = Field(default=None)
    execution_time_ms: float = Field(default=0.0)

    # ── Formatter ─────────────────────────────────────────────────────────
    formatted_answer: Optional[dict] = Field(default=None, description="Kết quả đã format")

    # ── Pipeline Control ─────────────────────────────────────────────────
    current_step: str = Field(default="start", description="Bước hiện tại trong pipeline")
    next_agent: str = Field(default="orchestrator", description="Agent tiếp theo")
    error: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=2)

    # ── History ────────────────────────────────────────────────────────────
    messages: list[dict] = Field(default_factory=list, description="Lịch sử messages giữa các agents")

    evidence: str = Field(default="", description="Kiến thức nghiệp vụ (Domain knowledge) từ BIRD dataset")
    def add_message(self, sender: str, content: Any):
        self.messages.append({
            "sender": sender,
            "content": content,
        })

    def model_post_init(self, _):
        # Đảm bảo messages là list
        if self.messages is None:
            self.messages = []
