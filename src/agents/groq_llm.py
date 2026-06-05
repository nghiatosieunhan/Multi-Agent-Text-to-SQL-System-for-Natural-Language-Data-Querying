import os
import time
import structlog
from typing import Any, Optional, Dict
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from src.config import config

log = structlog.get_logger("groq_llm")

def get_llm(model: str = "llama-3.3-70b-versatile", temperature: float = 0.0, **kwargs):
    """Lấy đối tượng ChatGroq từ Langchain."""
    return ChatGroq(
        model=model,
        temperature=temperature,
        api_key=os.getenv("GROQ_API_KEY") or getattr(config, "GROQ_API_KEY", None),
        **kwargs
    )

def invoke(prompt: str, model: str = None, temperature: float = 0.0, max_tokens: int = 1024, system_prompt: str = None) -> str:
    """Gọi LLM đồng bộ bằng Groq API với cơ chế tự thử lại."""
    if not model:
        model = getattr(config, "LLM_MODEL_PRO", "llama-3.3-70b-versatile")
        
    llm = get_llm(model=model, temperature=temperature, max_tokens=max_tokens)
    
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))
    
    max_retries = 15
    for attempt in range(max_retries):
        try:
            response = llm.invoke(messages)
            return response.content
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait_time = 15
                log.warning("groq_rate_limit", attempt=attempt+1, wait=wait_time, error=str(e)[:100])
                time.sleep(wait_time)
            elif attempt == max_retries - 1:
                log.error("groq_invoke_failed", error=str(e))
                raise
            else:
                time.sleep(3)
    return ""

async def ainvoke(prompt: str, model: str = None, temperature: float = 0.0, max_tokens: int = 1024, system_prompt: str = None) -> str:
    """Gọi LLM bất đồng bộ bằng Groq API với cơ chế tự thử lại."""
    if not model:
        model = getattr(config, "LLM_MODEL_PRO", "llama-3.3-70b-versatile")
        
    llm = get_llm(model=model, temperature=temperature, max_tokens=max_tokens)
    
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            return response.content
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                log.warning("groq_rate_limit", attempt=attempt+1, error=str(e))
                time.sleep(2 ** attempt)
            elif attempt == max_retries - 1:
                log.error("groq_ainvoke_failed", error=str(e))
                raise
            else:
                time.sleep(1)
    return ""
