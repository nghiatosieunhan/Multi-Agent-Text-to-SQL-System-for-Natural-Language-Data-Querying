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
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "chinook" / "Chinook_Sqlite.sqlite"))
    CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(BASE_DIR / "chroma_db"))
    LOG_DIR = BASE_DIR / "logs"
    LOG_DIR.mkdir(exist_ok=True)

    # ── Multi-DB ─────────────────────────────────────────────────────────
    SCHEMA_CACHE_DIR = BASE_DIR / "schemas"
    SCHEMA_CACHE_DIR.mkdir(exist_ok=True)

    # ── API Keys ────────────────────────────────────────────────────────
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    # LLM Models — đọc từ .env hoặc dùng default
    LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    LLM_MODEL_FALLBACK = os.getenv("LLM_MODEL_FALLBACK", "llama-3.1-8b-instant")

    # Embedding Model — Gemini embedding
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # ── System ───────────────────────────────────────────────────────────
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
    GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # ── Semantic Cache ───────────────────────────────────────────────────
    CACHE_SIMILARITY_THRESHOLD = 0.92  # Ngưỡng similarity để cache hit
    CACHE_MAX_SIZE = 500               # Số entry tối đa trong cache

    # ── Agent Config ────────────────────────────────────────────────────
    MAX_WORKER_RETRIES = 2
    WORKER_TEMPERATURE = 0.0          # Deterministic output
    ORCHESTRATOR_TEMPERATURE = 0.3


config = Config()
