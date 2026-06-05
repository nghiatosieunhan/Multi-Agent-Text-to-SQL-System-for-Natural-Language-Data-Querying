import streamlit as st
import tempfile
from pathlib import Path
from src.config import config
from src.db import get_db_manager
from src.memory import get_semantic_cache
from app.state import get_db_path, init_or_switch_db

def render_sidebar():
    with st.sidebar:
        st.title("🔍 Text2SQL")
        st.markdown("**Multi-Database System**")
        st.divider()

        # ── Chat History (Sessions) ──────────────────────────────────────────────
        from app.history_manager import load_all_sessions
        import uuid
        
        st.subheader("💬 Quản lý Trò chuyện")
        
        if st.button("➕ Cuộc trò chuyện mới", type="primary", use_container_width=True):
            st.session_state.messages = []
            st.session_state.session_id = str(uuid.uuid4())
            st.rerun()
            
        st.caption("LỊCH SỬ TRÒ CHUYỆN")
        sessions = load_all_sessions()
        
        # Sắp xếp sessions theo thời gian mới nhất
        sorted_sessions = sorted(sessions.items(), key=lambda x: x[1]['updated_at'], reverse=True)
        
        if not sorted_sessions:
            st.write("Chưa có lịch sử.")
        else:
            with st.container(height=250, border=False):
                for sid, sdata in sorted_sessions[:20]: # Hiển thị 20 chat gần nhất, có thanh cuộn
                    title = sdata.get("title", "Trò chuyện mới")
                    if len(title) > 25:
                        title = title[:25] + "..."
                    
                    # Highlight current session
                    is_current = (sid == st.session_state.session_id)
                    btn_label = f"📝 {title}" if is_current else f"🕒 {title}"
                    
                    # type="tertiary" removes the box border, making it look like a sleek list item
                    if st.button(btn_label, key=f"hist_{sid}", use_container_width=True, type="tertiary"):
                        st.session_state.session_id = sid
                        st.session_state.messages = sdata["messages"]
                        st.rerun()

        st.divider()

    # ── Database Selector ──────────────────────────────────────────────────
        st.subheader("📂 Database")

        # Load metadata cho các DB đã có sẵn (để lấy tên đẹp)
        try:
            from src.agents.onboard import _load_registry
            registry = _load_registry()
        except Exception:
            registry = {}

        db_options = []
        db_labels = {}

        # 1. QUÉT ĐỘNG TẤT CẢ CÁC FILE SQLITE TRONG THƯ MỤC DATA
        # Bất kỳ file nào được upload vào thư mục này đều sẽ hiện lên
        scan_directories = [config.DATA_DIR, config.UPLOAD_DIR]
        
        for directory in scan_directories:
            if directory.exists():
                for db_file in directory.glob("*.*"):
                    if db_file.suffix.lower() in [".sqlite", ".db", ".sqlite3"]:
                        path_str = str(db_file)
                        if path_str not in db_options: # Tránh bị thêm trùng lặp
                            db_options.append(path_str)
                            db_labels[path_str] = db_file.name

        # 2. Cập nhật tên hiển thị đẹp hơn cho các DB đã được đăng ký (Registry)
        for h, meta in registry.items():
            if isinstance(meta, dict):
                path = meta.get("db_path", str(config.DATA_DIR / f"{h}.sqlite"))
                db_name = meta.get("db_name", h)
                table_count = meta.get("table_count", "?")
                if path in db_labels:
                    db_labels[path] = f"{db_name} ({table_count} tables)"

        # # 3. Đảm bảo Database mặc định (Chinook) luôn nằm ở vị trí đầu tiên
        # default_db = str(config.DB_PATH)
        # if default_db in db_options:
        #     db_options.remove(default_db)
        # db_options.insert(0, default_db)
        # if default_db not in db_labels or db_labels[default_db] == Path(default_db).name:
        #     db_labels[default_db] = "Chinook (default)"

        # 4. Hiển thị Selectbox
        current = get_db_path()
        selected = st.selectbox(
            "Active database:",
            options=db_options,
            format_func=lambda p: db_labels.get(p, Path(p).name),
            index=db_options.index(current) if current in db_options else 0,
        )

        # Tự động switch ngay khi người dùng chọn DB khác
        if selected != current:
            init_or_switch_db(selected)
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
            # Lộ trình lưu file: Bỏ dùng file tmp ngẫu nhiên, lưu thẳng bằng tên gốc
            target_path = config.UPLOAD_DIR / uploaded.name
            with open(target_path, "wb") as f:
                f.write(uploaded.getbuffer())
            tmp_path = str(target_path)

            st.success(f"Uploaded: {uploaded.name}")

            # Auto-onboard
            if st.button("Onboard & Use", use_container_width=True):
                with st.spinner("Đang quét Schema và tự động phân tích ý nghĩa các bảng..."):
                    init_or_switch_db(tmp_path, force_refresh=True)
                
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
        
        @st.cache_data(ttl=3600)
        def _cached_get_schema(path: str):
            db_sidebar = get_db_manager(path)
            # convert to dict or just return since pydantic/dataclass can be cached
            return db_sidebar.get_schema()

        try:
            schema_sidebar = _cached_get_schema(get_db_path())
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



        # ── Developer Tools ──────────────────────────────────────────────────────
        with st.expander("🛠️ Developer Tools", expanded=False):
            # Cache stats
            cache = get_semantic_cache()
            s = cache.stats()
            st.subheader("📦 Semantic Cache")
            
            new_threshold = st.slider(
                "Độ nhạy (Similarity Threshold)", 
                min_value=0.5, max_value=1.0, value=cache.threshold, step=0.01,
                help="Giảm xuống để dễ 'Hit' cache hơn (chấp nhận câu hỏi na ná nhau). Tăng lên 1.0 yêu cầu phải giống hệt."
            )
            if new_threshold != cache.threshold:
                cache.threshold = new_threshold

            col1, col2, col3 = st.columns(3)
            col1.metric("Size", f"{s['size']}/{s['max_size']}")
            col2.metric("Hits", s["hits"])
            col3.metric("Misses", s["misses"])

            if st.button("Xóa Cache", use_container_width=True, key="dev_clear_cache"):
                cache.invalidate()
                st.rerun()

            st.divider()

            # API Status
            st.subheader("🔑 API")
            if config.GROQ_API_KEY or config.LLM_PROVIDER == "google":
                st.success(f"Provider: {config.LLM_PROVIDER.upper()}")
            else:
                st.error("No API key configured!")
            st.caption(f"Model Pro: `{config.LLM_MODEL_PRO}`")
            st.caption(f"Embedding: `{config.EMBEDDING_MODEL}`")
