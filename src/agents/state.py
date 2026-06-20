"""
LangGraph State — defines state schema for multi-agent graph.
"""
from typing import Optional, Any, Annotated
import operator
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from src.schema import SchemaContext
from src.config import config


class AgentState(BaseModel):
    """
    Shared state across all agents in LangGraph.
    Fields are updated throughout the pipeline.
    """
    # ── Conversational & Pre-processing Layer ─────────────────────────────
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)
    extracted_entities: dict = Field(default_factory=dict, description="Entities extracted via Regex")
    is_fast_route: bool = Field(default=False, description="Flag for Regex fast route bypassing Planner")

    # ── Input ─────────────────────────────────────────────────────────────
    user_question: str = Field(default="", description="Original user question")
    session_id: str = Field(default="default", description="Session ID for tracking")
    analysis_mode: str = Field(default="deep", description="Mode: 'fast' (no insight) or 'deep' (full LLM insight)")
    evaluation_profile: str = Field(default="full", description="Named benchmark/ablation profile")
    evaluation_options: dict = Field(default_factory=dict, description="Per-run feature toggles")
    telemetry: dict = Field(default_factory=dict, description="LLM usage and per-node timings")

    # ── Multi-DB ──────────────────────────────────────────────────────────
    current_db_path: str = Field(default="", description="Current Database URI or path")
    db_dialect: str = Field(default="sqlite", description="Database dialect (sqlite, postgresql, mysql)")
    current_db_schema: Optional[SchemaContext] = Field(
        default=None, description="Onboarded schema context for the current DB"
    )

    # ── Orchestrator ──────────────────────────────────────────────────────
    intent_type: str = Field(default="unknown", description="Intent type: simple|aggregate|join|complex|ambiguous")
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    orchestrator_reasoning: str = Field(default="")

    # ── Cache ─────────────────────────────────────────────────────────────
    cache_checked: bool = Field(default=False)
    cache_hit: bool = Field(default=False)
    cached_result: Optional[dict] = Field(default=None, description="Cached result")

    # ── Schema / RAG ──────────────────────────────────────────────────────
    schema_context: str = Field(default="", description="Schema context from RAG retrieval")
    override_schema_context: Optional[str] = Field(
        default=None,
        description="Override schema — replaces RAG retrieval (Spider evaluation)"
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

    # ── Formatter & Analytics ──────────────────────────────────────────────
    formatted_answer: Optional[dict] = Field(default=None, description="Kết quả đã format")
    suggested_charts: Optional[dict] = Field(default=None, description="Cấu hình biểu đồ được gợi ý")
    suggestions: list[str] = Field(default_factory=list, description="Các câu hỏi gợi ý tiếp theo")

    # ── Pipeline Control ─────────────────────────────────────────────────
    current_step: str = Field(default="start", description="Bước hiện tại trong pipeline")
    next_agent: str = Field(default="orchestrator", description="Agent tiếp theo")
    error: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default_factory=lambda: config.MAX_WORKER_RETRIES)

    evidence: str = Field(default="", description="Kiến thức nghiệp vụ (Domain knowledge) từ BIRD dataset")
