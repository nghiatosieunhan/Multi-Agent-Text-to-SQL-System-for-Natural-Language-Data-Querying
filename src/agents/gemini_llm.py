"""
Google Cloud Vertex AI (Gemini) Wrapper
Hỗ trợ invoke và ainvoke tương tự groq_llm.py
"""
import structlog
from typing import Optional, Dict, Any
from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import SystemMessage, HumanMessage
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from src.config import config

log = structlog.get_logger("gemini_llm")

def get_llm(model: str = None, temperature: float = 0.0, max_tokens: int = 4096) -> ChatVertexAI:
    """Khởi tạo ChatVertexAI client."""
    model_name = model or config.LLM_MODEL_PRO
    return ChatVertexAI(
        model_name=model_name,
        temperature=temperature,
        max_output_tokens=max_tokens,
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
    )

@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def invoke(
    prompt: str,
    model: str = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    system_prompt: str = None,
    **kwargs
) -> str:
    """Gọi Gemini đồng bộ với retry."""
    llm = get_llm(model=model, temperature=temperature, max_tokens=max_tokens)
    
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))
    
    try:
        response = llm.invoke(messages)
        finish_reason = response.response_metadata.get("finish_reason", "unknown") if hasattr(response, "response_metadata") else "unknown"
        if finish_reason != "STOP":
            log.warning("gemini_abnormal_finish", finish_reason=finish_reason, content_length=len(response.content))
            # If it hits MAX_TOKENS but output is tiny, it's a silent Vertex AI quota drop!
            if finish_reason == "MAX_TOKENS" and len(response.content) < max_tokens:
                raise RuntimeError(f"Vertex AI silent TPM drop detected: MAX_TOKENS at len {len(response.content)}")
        return response.content
    except Exception as e:
        log.warning("gemini_invoke_failed", error=str(e), model=model)
        raise e

@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
async def ainvoke(
    prompt: str,
    model: str = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    system_prompt: str = None,
    **kwargs
) -> str:
    """Gọi Gemini bất đồng bộ với retry."""
    llm = get_llm(model=model, temperature=temperature, max_tokens=max_tokens)
    
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))
    
    try:
        response = await llm.ainvoke(messages)
        return response.content
    except Exception as e:
        log.warning("gemini_ainvoke_failed", error=str(e), model=model)
        raise e
