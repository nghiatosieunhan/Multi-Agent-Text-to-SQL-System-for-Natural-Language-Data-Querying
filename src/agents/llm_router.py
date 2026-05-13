"""
Dynamic LLM Router
Cho phép chuyển đổi linh hoạt giữa Groq và Google Cloud (Gemini) dựa trên .env
"""
from typing import Any
import structlog
from src.config import config

log = structlog.get_logger("llm_router")

# Chọn Provider
PROVIDER = getattr(config, "LLM_PROVIDER", "groq").lower()

if PROVIDER == "google":
    from src.agents.gemini_llm import invoke, ainvoke, get_llm
    log.info("llm_provider_initialized", provider="google_vertex_ai")
else:
    from src.agents.llm_router import invoke, ainvoke, get_llm
    log.info("llm_provider_initialized", provider="groq")

__all__ = ["invoke", "ainvoke", "get_llm"]
