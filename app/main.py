import sys
from pathlib import Path
import logging
import structlog

# Thêm gốc thư mục dự án vào sys.path để có thể import src
sys.path.insert(0, str(Path(__file__).parent.parent))

# Fix cho Streamlit: Chuyển structlog sang dùng native logging của Python
logging.basicConfig(level=logging.INFO, format="%(message)s")
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer()
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=False
)

import streamlit as st
from app.styles import load_css
from app.state import init_session
from app.sidebar_ui import render_sidebar
from app.chat_ui import render_chat_history, handle_user_input

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Text2SQL — Multi-DB",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_css()
init_session()

render_sidebar()

# ── Main Area ─────────────────────────────────────────────────────────────────
st.title("✨ Xin chào, tôi có thể giúp gì cho bạn?")
st.caption("Trợ lý Dữ liệu AI (The Sovereign Associate) - Powered by Gemini")

render_chat_history()
handle_user_input()
