import json
import hashlib
from pathlib import Path
import structlog
from src.config import config
from src.db import DatabaseManager
from src.agents.llm_router import invoke

log = structlog.get_logger("onboard")

def _db_hash(db_path: str) -> str:
    hasher = hashlib.md5()
    hasher.update(str(db_path).encode('utf-8'))
    return hasher.hexdigest()

def _load_registry() -> dict:
    registry_path = config.DATA_DIR / "registry.json"
    if registry_path.exists():
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def list_databases() -> dict:
    return _load_registry()

def load_cached(db_path: str) -> dict:
    db_id = _db_hash(str(db_path))
    cache_path = config.DATA_DIR / f"semantic_cache_{db_id}.json"
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(db_path: str, semantic_dict: dict):
    db_id = _db_hash(str(db_path))
    cache_path = config.DATA_DIR / f"semantic_cache_{db_id}.json"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(semantic_dict, f, ensure_ascii=False, indent=4)

def get_current_db_schema(db_path: str):
    db = DatabaseManager(str(db_path))
    schema = db.get_schema()
    semantic_dict = load_cached(db_path)
    
    if not semantic_dict:
        semantic_dict = {t.table_name: f"Bảng {t.table_name}" for t in schema.tables}
        
    return schema, semantic_dict

def generate_descriptions(db_path: str) -> dict:
    db = DatabaseManager(str(db_path))
    schema = db.get_schema()
    semantic_dict = {}
    for t in schema.tables:
        prompt = f"Mô tả ngắn gọn ý nghĩa của bảng {t.table_name} dựa trên các cột: {[c['name'] for c in t.columns]}"
        try:
            desc = invoke(prompt, temperature=0.0)
            semantic_dict[t.table_name] = desc.strip()
        except Exception:
            semantic_dict[t.table_name] = f"Bảng {t.table_name}"
    return semantic_dict

def onboard_db(db_path: str, db_id: str, description: str):
    log.info("onboarding", db=db_id, path=db_path)
    registry = _load_registry()
    registry[db_id] = description
    
    registry_path = config.DATA_DIR / "registry.json"
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=4)
        
    semantic_dict = generate_descriptions(db_path)
    save_cache(db_path, semantic_dict)
    log.info("onboard_success")
