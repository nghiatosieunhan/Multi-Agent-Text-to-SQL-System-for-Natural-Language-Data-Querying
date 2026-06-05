"""
Configuration cho Multi-Agent Text-to-SQL System.
Load từ biến môi trường (.env).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    """Cấu hình hệ thống — tất cả giá trị đọc từ .env"""

    # ── Paths ──────────────────────────────────────────────────────────
    BASE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR / "data"
    UPLOAD_DIR = DATA_DIR / "uploaded_data"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True) # Tự động tạo folder nếu chưa có
    _default_db = next(DATA_DIR.glob("*.sqlite"), "") if DATA_DIR.exists() else ""
    DB_PATH = os.getenv("DB_PATH", str(_default_db))
    FAISS_PERSIST_DIR = os.getenv("FAISS_PERSIST_DIR", str(BASE_DIR / "faiss_unified_fewshot_db"))
    LOG_DIR = BASE_DIR / "logs"
    LOG_DIR.mkdir(exist_ok=True)

    # ── API Keys ────────────────────────────────────────────────────────
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    # LLM Models — Multi-tier architecture
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()
    LLM_MODEL_PRO = os.getenv("LLM_MODEL_PRO", "llama-3.3-70b-versatile")
    LLM_MODEL_FLASH = os.getenv("LLM_MODEL_FLASH", "llama-3.1-8b-instant")

    # Embedding Model — Gemini embedding
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # ── System ───────────────────────────────────────────────────────────
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
    GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # ── Semantic Cache ───────────────────────────────────────────────────
    CACHE_SIMILARITY_THRESHOLD = 0.92  
    CACHE_MAX_SIZE = 500              

    # ── Agent Config ────────────────────────────────────────────────────
    MAX_WORKER_RETRIES = 2
    WORKER_TEMPERATURE = 0.0         
    ORCHESTRATOR_TEMPERATURE = 0.3


config = Config()
