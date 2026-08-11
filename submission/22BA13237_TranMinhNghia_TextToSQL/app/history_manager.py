import json
import uuid
import datetime
from pathlib import Path
from src.config import config

HISTORY_FILE = Path(config.DATA_DIR) / "chat_history.json"

def load_all_sessions():
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_all_sessions(sessions):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

def save_session(session_id, title, messages):
    sessions = load_all_sessions()
    sessions[session_id] = {
        "title": title,
        "updated_at": datetime.datetime.now().isoformat(),
        "messages": messages
    }
    save_all_sessions(sessions)

def get_session(session_id):
    sessions = load_all_sessions()
    return sessions.get(session_id, None)

def delete_session(session_id):
    sessions = load_all_sessions()
    if session_id in sessions:
        del sessions[session_id]
        save_all_sessions(sessions)
