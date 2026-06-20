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
# Hàng nút công cụ góc phải trên cùng (cạnh khu vực Deploy)
col_spacer, col_mode, col_clear = st.columns([6, 2, 2])
with col_mode:
    analysis_mode = st.selectbox(
        "Chế độ phân tích:",
        ["⚡ Tiêu chuẩn", "🧠 Chuyên sâu (Pro)"],
        label_visibility="collapsed",
        key="analysis_mode_selector"
    )
with col_clear:
    if st.button("🗑️ Xóa trò chuyện", use_container_width=True):
        from app.history_manager import delete_session
        delete_session(st.session_state.session_id)
        st.session_state.messages = []
        import uuid
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

if "Pro" in st.session_state.analysis_mode_selector:
    st.info("🚀 Tính năng Phân tích Chuyên sâu đang được phát triển. Tạm thời chạy chế độ Tiêu chuẩn.")

st.title("✨ Xin chào, tôi có thể giúp gì cho bạn?")
st.caption("Trợ lý Dữ liệu AI (The Sovereign Associate) - Powered by Gemini")

render_chat_history()
handle_user_input()
