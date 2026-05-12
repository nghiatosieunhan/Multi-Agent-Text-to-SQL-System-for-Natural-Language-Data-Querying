"""
Streamlit Web UI cho Multi-Agent Text-to-SQL System.
Hỗ trợ multi-database: upload SQLite file, switch giữa các DB đã onboard.

Chạy: streamlit run app.py
"""
import sys
import time
import json
import tempfile
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.config import config
from src.db import get_db_manager
from src.rag import rebuild_schema_index
from src.graph import run_query
from src.memory import get_semantic_cache
# from src.tools.visualizer import plot_chart, render_table_ascii
from src.agents.onboard import get_current_db_schema, list_databases as onboard_list_dbs

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Text2SQL — Multi-DB",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 1. Nhúng Font chữ chuyên nghiệp (Inter) */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"], .stMarkdown p, .stCodeBlock code {
    font-family: 'Inter', sans-serif !important;
    font-size: 14px !important;
}

/* 2. Giảm kích thước Tiêu đề (Headings) */
h1 { font-size: 24px !important; font-weight: 600 !important; }
h2 { font-size: 20px !important; font-weight: 600 !important; }
h3 { font-size: 16px !important; font-weight: 600 !important; }

/* 3. Tinh chỉnh khung tin nhắn Chat (Chat Bubbles) */
.stChatMessage {
    padding: 1rem !important;
    border-radius: 12px !important;
    background-color: #f8f9fa; /* Màu xám nhạt thanh lịch */
    border: 1px solid #eaebed;
    margin-bottom: 15px;
}

/* 4. Tinh chỉnh khung nhập liệu (Chat Input) */
.stChatInputContainer {
    padding-bottom: 20px;
}
.stChatInputContainer > div {
    border-radius: 20px !important;
    border: 1px solid #cbd5e1 !important;
    box-shadow: 0px 4px 6px rgba(0, 0, 0, 0.05) !important;
}

/* 5. Ẩn các thành phần mặc định của Streamlit */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ── Session State ─────────────────────────────────────────────────────────────
def _get_db_path():
    return st.session_state.get("current_db_path", config.DB_PATH)


def _init_or_switch_db(db_path: str, force_refresh: bool = False):
    """Switch to a different database: reinit DB manager + rebuild schema index."""
    if db_path == _get_db_path() and not force_refresh:
        return

    st.session_state.current_db_path = db_path

    # Reinit DB manager
    get_db_manager(db_path)

    # Rebuild schema index with semantic descriptions
    try:
        schema, _ = get_current_db_schema(db_path, force_refresh=force_refresh)
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
    if "system_ready" not in st.session_state:
        st.session_state.system_ready = False
    if "current_db_path" not in st.session_state:
        st.session_state.current_db_path = config.DB_PATH
    if "schema_info" not in st.session_state:
        st.session_state.schema_info = None


init_session()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _run_query(question: str, db_path: str):
    """Wrapper để pass db_path vào run_query."""
    start = time.time()
    result = run_query(question, session_id=f"ui_{int(time.time())}", db_path=db_path)
    elapsed = time.time() - start
    return result, elapsed

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔍 Text2SQL")
    st.markdown("**Multi-Database System**")
    st.divider()

    # ── Database Selector ──────────────────────────────────────────────────
    st.subheader("📂 Database")

    # List onboarded databases
    try:
        from src.agents.onboard import _load_registry
        registry = _load_registry()
    except Exception:
        registry = {}

    db_options = [config.DB_PATH]  # Default Chinook
    db_labels = {config.DB_PATH: f"Chinook (default)"}
    for h, meta in registry.items():
        if isinstance(meta, dict):
            path = meta.get("db_path", str(config.DATA_DIR / f"{h}.sqlite"))
            db_name = meta.get("db_name", h)
            table_count = meta.get("table_count", "?")
            label = f"{db_name} ({table_count} tables)"
        else:
            path = str(config.DATA_DIR / f"{h}.sqlite")
            label = f"{h}"

        if path not in db_labels:
            db_options.append(path)
            db_labels[path] = label

    current = _get_db_path()
    selected = st.selectbox(
        "Active database:",
        options=db_options,
        format_func=lambda p: db_labels.get(p, Path(p).name),
        index=db_options.index(current) if current in db_options else 0,
    )

    if st.button("Switch DB", use_container_width=True):
        if selected != current:
            _init_or_switch_db(selected)
            st.rerun()

    # ── Upload New DB ────────────────────────────────────────────────────
    st.divider()
    st.subheader("⬆️ Upload SQLite File")
    uploaded = st.file_uploader(
        "Upload .sqlite / .db file",
        type=["sqlite", "db", "sqlite3"],
        help="Upload a SQLite database file",
    )

    if uploaded:
        # Save to temp file
        suffix = Path(uploaded.name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name

        st.success(f"Uploaded: {uploaded.name}")

        # Auto-onboard
        if st.button("Onboard & Use", use_container_width=True):
            with st.spinner("Đang quét Schema và tự động phân tích ý nghĩa các bảng..."):
                _init_or_switch_db(tmp_path, force_refresh=True)
            
            with st.spinner("Đang chạy Synthetic Few-Shot: Tự động sinh 15 kinh nghiệm ảo nạp vào FAISS..."):
                try:
                    from src.agents.auto_fewshot import auto_generate_and_index_fewshot
                    dataset_type = Path(tmp_path).stem
                    auto_generate_and_index_fewshot(tmp_path, dataset_type, count=15)
                except Exception as e:
                    st.warning(f"Lỗi khi sinh Few-shot: {e}")
                    
            st.session_state.uploaded_tmp = tmp_path
            st.rerun()

    # Show current DB info
    if st.session_state.schema_info:
        info = st.session_state.schema_info
        st.success(f"Active: {info['db_name']} — {info['tables']} tables")
    elif st.session_state.current_db_path:
        st.info(f"DB: {Path(st.session_state.current_db_path).stem}")

    st.divider()

    # Schema viewer
    st.subheader("📋 Schema")
    try:
        db_sidebar = get_db_manager(_get_db_path())
        schema_sidebar = db_sidebar.get_schema()
        for t in schema_sidebar.tables:
            with st.expander(f"📋 {t.table_name}"):
                st.caption(f"Rows: {t.row_count or '?'}")
                cols_info = [f"`{c['name']}` `{c['type'] or 'TEXT'}`" for c in t.columns]
                for c in cols_info[:10]:
                    st.markdown(f"  {c}")
                if len(cols_info) > 10:
                    st.caption(f"... +{len(cols_info)-10} more")
    except Exception as e:
        st.warning(f"Schema: {e}")

    st.divider()

    # Cache stats
    cache = get_semantic_cache()
    s = cache.stats()
    st.subheader("📦 Semantic Cache")
    
    new_threshold = st.slider(
        "Độ nhạy (Similarity Threshold)", 
        min_value=0.5, max_value=1.0, value=cache.threshold, step=0.05,
        help="Giảm xuống để dễ 'Hit' cache hơn (chấp nhận câu hỏi na ná nhau). Tăng lên 1.0 yêu cầu phải giống hệt."
    )
    if new_threshold != cache.threshold:
        cache.threshold = new_threshold

    col1, col2, col3 = st.columns(3)
    col1.metric("Size", f"{s['size']}/{s['max_size']}")
    col2.metric("Hits", s["hits"])
    col3.metric("Misses", s["misses"])

    if st.button("Xóa Cache", use_container_width=True):
        cache.invalidate()
        st.rerun()

    st.divider()

    # API Status
    st.subheader("🔑 API")
    if config.GROQ_API_KEY:
        st.success(f"Groq: {config.LLM_MODEL}")
    else:
        st.error("No Groq API key!")
    st.caption(f"Model: `{config.LLM_MODEL}`")
    st.caption(f"Embedding: `{config.EMBEDDING_MODEL}`")


# ── Main Area ─────────────────────────────────────────────────────────────────
st.title("✨ Xin chào, tôi có thể giúp gì cho bạn?")
st.caption("Trợ lý Dữ liệu AI (The Sovereign Associate) - Powered by Gemini")

# Nút xóa lịch sử trò chuyện
col_spacer, col_clear = st.columns([5, 1])
with col_clear:
    if st.button("🗑️ Xóa trò chuyện", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Khởi tạo lịch sử chat nếu chưa có
if "messages" not in st.session_state:
    st.session_state.messages = []

# Hiển thị các tin nhắn cũ
for i, msg in enumerate(st.session_state.messages):
    avatar = "👤" if msg["role"] == "user" else "✨"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        
        # Nếu là assistant thì hiện thêm phần SQL và Data (nếu có)
        if msg["role"] == "assistant":
            if msg.get("sql"):
                with st.expander("🔍 Xem câu lệnh SQL"):
                    st.code(msg["sql"], language="sql")
                    
                    # Tính năng Auto-Learning (Phong cách Vanna AI)
                    if msg.get("user_prompt") and not msg.get("is_trained"):
                        if st.button("🧠 Dạy AI câu lệnh này (Lưu vào RAG)", key=f"train_btn_{i}"):
                            try:
                                from src.rag.few_shot_retriever import FewShotRetriever
                                retriever = FewShotRetriever()
                                retriever.add_single_example(msg["user_prompt"], msg["sql"], dataset_type="chinook_vn")
                                st.session_state.messages[i]["is_trained"] = True
                                st.toast("✅ Đã học câu lệnh này! Lần sau hỏi tương tự AI sẽ nhớ.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Lỗi: {e}")
            if msg.get("df") is not None:
                with st.expander("📊 Xem bảng dữ liệu"):
                    st.dataframe(msg["df"], use_container_width=True, hide_index=True)
                
                # Render chart từ lịch sử
                if msg.get("viz") and msg["viz"].get("recommended"):
                    viz = msg["viz"]
                    chart_type = viz.get("chart_type", "bar")
                    with st.expander(f"📈 Biểu đồ trực quan ({chart_type.upper()})"):
                        try:
                            df_chart = msg["df"].set_index(msg["df"].columns[0])
                            if chart_type in ["bar", "pie"]:
                                st.bar_chart(df_chart)
                            elif chart_type == "line":
                                st.line_chart(df_chart)
                            elif chart_type == "area":
                                st.area_chart(df_chart)
                            else:
                                st.bar_chart(df_chart)
                        except Exception:
                            st.caption("Biểu đồ không khả dụng cho dữ liệu này.")

# Khung nhập câu hỏi ở đáy màn hình
if prompt := st.chat_input("Đặt câu hỏi về dữ liệu..."):
    if not config.GROQ_API_KEY:
        st.error("Vui lòng cấu hình GROQ_API_KEY trong file .env")
        st.stop()

    # Thêm tin nhắn của User vào lịch sử và hiển thị lên màn hình
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    # Hiển thị UI của Assistant đang trả lời
    with st.chat_message("assistant", avatar="✨"):
        with st.status("✨ Đang suy luận và xử lý dữ liệu...", expanded=True) as status:
            st.write("Đang đọc schema và phân tích ngữ cảnh...")
            db_path = _get_db_path()
            result, elapsed = _run_query(prompt, db_path)
            status.update(label=f"Hoàn tất xử lý trong {elapsed:.2f}s", state="complete", expanded=False)
            
            answer_content = ""
            df = None
            sql = result.generated_sql if hasattr(result, 'generated_sql') else None
            
            # Phân tích kết quả trả về
            if hasattr(result, 'error') and result.error and not (hasattr(result, 'formatted_answer') and result.formatted_answer):
                answer_content = f"❌ **Lỗi:** {result.error}"
            elif hasattr(result, 'formatted_answer') and result.formatted_answer:
                fa = result.formatted_answer
                if "chat_response" in fa:
                    answer_content += fa["chat_response"]
                else:
                    answer_content += f"**{fa.get('summary', '')}**\n\n"
                    if fa.get("detailed_answer"):
                        answer_content += f"{fa['detailed_answer']}\n\n"
                    if fa.get("insights"):
                        answer_content += "💡 **Insights:**\n"
                        for insight in fa["insights"]:
                            answer_content += f"- {insight}\n"
            elif hasattr(result, 'query_result') and result.query_result:
                answer_content = f"✅ Truy vấn thành công ({result.query_result.get('row_count', 0)} dòng dữ liệu)."

            # In câu trả lời chính
            st.markdown(answer_content)
            
            # Tạo Expander cho SQL
            if sql:
                with st.expander("🔍 Xem câu lệnh SQL"):
                    st.code(sql, language="sql")
            
            # Lấy cấu hình biểu đồ từ LLM
            viz = {}
            if hasattr(result, 'formatted_answer') and result.formatted_answer:
                viz = result.formatted_answer.get("visualization", {})
                
            # Tạo Expander cho bảng dữ liệu Pandas và vẽ biểu đồ
            if hasattr(result, 'query_result') and result.query_result and result.query_result.get("rows"):
                rows = result.query_result["rows"]
                cols = result.query_result["columns"]
                try:
                    df = pd.DataFrame(rows, columns=cols)
                    with st.expander("📊 Xem bảng dữ liệu"):
                        st.dataframe(df, use_container_width=True, hide_index=True)
                        
                    # Tự động vẽ biểu đồ bằng thư viện native của Streamlit
                    if viz.get("recommended"):
                        chart_type = viz.get("chart_type", "bar")
                        with st.expander(f"📈 Biểu đồ trực quan ({chart_type.upper()})"):
                            try:
                                # Lấy cột đầu tiên làm trục X, các cột còn lại làm giá trị Y
                                df_chart = df.set_index(df.columns[0])
                                if chart_type in ["bar", "pie"]:
                                    st.bar_chart(df_chart)
                                elif chart_type == "line":
                                    st.line_chart(df_chart)
                                elif chart_type == "area":
                                    st.area_chart(df_chart)
                                else:
                                    st.bar_chart(df_chart)
                            except Exception as e:
                                st.caption("Dữ liệu không phù hợp để vẽ biểu đồ.")
                except Exception as e:
                    st.warning(f"Không thể hiển thị bảng: {e}")

            # Lưu lại câu trả lời vào lịch sử
            st.session_state.messages.append({
                "role": "assistant", 
                "content": answer_content,
                "sql": sql,
                "df": df,
                "viz": viz,
                "user_prompt": prompt,
                "is_trained": False
            })

