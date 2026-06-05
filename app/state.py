import streamlit as st
from pathlib import Path
from src.config import config
from src.db import get_db_manager
from src.rag import rebuild_schema_index
from src.agents.onboard import get_current_db_schema

def get_db_path():
    return st.session_state.get("current_db_path", config.DB_PATH)

def init_or_switch_db(db_path: str, force_refresh: bool = False):
    """Switch to a different database: reinit DB manager + rebuild schema index."""
    if db_path == get_db_path() and not force_refresh:
        return

    st.session_state.current_db_path = db_path

    # Reinit DB manager
    get_db_manager(db_path)

    # Rebuild schema index with semantic descriptions
    try:
        schema, _ = get_current_db_schema(db_path)
        rebuild_schema_index(get_db_manager(db_path), db_path=db_path)
        st.session_state.system_ready = True
        st.session_state.schema_info = {
            "tables": len(schema.tables),
            "db_name": Path(db_path).stem,
        }
    except Exception as e:
        st.session_state.system_ready = False
        st.warning(f"Schema init failed: {e}")

def init_session():
    if "history" not in st.session_state:
        st.session_state.history = []
    if "messages" not in st.session_state:
        st.session_state.messages = []
        
    if "session_id" not in st.session_state:
        import uuid
        st.session_state.session_id = str(uuid.uuid4())
    if "system_ready" not in st.session_state:
        st.session_state.system_ready = False
    if "current_db_path" not in st.session_state:
        st.session_state.current_db_path = config.DB_PATH
    if "schema_info" not in st.session_state:
        st.session_state.schema_info = None
