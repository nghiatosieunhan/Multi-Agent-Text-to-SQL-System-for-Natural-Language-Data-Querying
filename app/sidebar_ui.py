import streamlit as st
import tempfile
import time
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

        # Add current Cloud DB to options if it's active
        current = get_db_path()
        if current.startswith("postgresql") or current.startswith("mysql"):
            if current not in db_options:
                db_options.append(current)
                # Mask the password for display
                masked = current.split("@")[-1] if "@" in current else current
                db_labels[current] = f"☁️ {masked}"

        # 4. Hiển thị Selectbox
        selected = st.selectbox(
            "Active database:",
            options=db_options,
            format_func=lambda p: db_labels.get(p, p if p.startswith("http") else Path(p).name),
            index=db_options.index(current) if current in db_options else 0,
        )

        # Tự động switch ngay khi người dùng chọn DB khác
        if selected != current:
            init_or_switch_db(selected)
            st.rerun()

        # ── Connect Cloud DB ──────────────────────────────────────────────────
        st.divider()
        st.subheader("☁️ Connect Cloud DB")
        with st.form("cloud_db_form"):
            db_uri = st.text_input("Connection String (PostgreSQL / MySQL)", placeholder="postgresql://user:pass@host:port/dbname")
            submit_btn = st.form_submit_button("Kết nối")
            if submit_btn and db_uri:
                with st.spinner("Đang kết nối và quét Schema..."):
                    try:
                        init_or_switch_db(db_uri, force_refresh=True)
                        st.success("Kết nối thành công!")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi kết nối: {e}")

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

        # Thêm tính năng Xóa Data cho các file upload (chỉ áp dụng cho file local)
        current_db = get_db_path()
        if current_db and not (current_db.startswith("postgresql") or current_db.startswith("mysql") or current_db.startswith("http")):
            if st.button("🗑️ Xóa Database hiện tại", use_container_width=True):
                import os
                try:
                    # Chỉ xóa cache thuộc database sắp bị xóa.
                    cache = get_semantic_cache()
                    try:
                        cache.invalidate(namespace=current_db)
                    except TypeError:
                        cache.invalidate()
                    
                    # Dispose SQLAlchemy engine connections to release file lock (WinError 32)
                    db_manager = get_db_manager(current_db)
                    db_manager.engine.dispose()
                    
                    # Cần một chút thời gian cho HĐH giải phóng handle
                    time.sleep(0.5)
                    
                    if os.path.exists(current_db):
                        os.remove(current_db)
                    
                    st.toast(f"Đã xóa vĩnh viễn {Path(current_db).name}")
                    # Quay về mặc định
                    init_or_switch_db(str(config.DB_PATH))
                    st.rerun()
                except Exception as e:
                    st.error(f"Không thể xóa file (Có thể đang bị tiến trình khác chiếm giữ): {e}")

        st.divider()

        # Schema viewer
        st.subheader("📋 Schema")
        
        @st.cache_resource(ttl=3600)
        def _cached_get_schema(path: str):
            db_sidebar = get_db_manager(path)
            # convert to dict or just return since pydantic/dataclass can be cached
            return db_sidebar.get_schema()

        try:
            schema_sidebar = _cached_get_schema(get_db_path())
            table_names = [t.table_name for t in schema_sidebar.tables]
            
            selected_table = st.selectbox("Tra cứu bảng (Table):", table_names)
            if selected_table:
                t = next(t for t in schema_sidebar.tables if t.table_name == selected_table)
                st.caption(f"📊 Dữ liệu: {t.row_count or '?'} dòng")
                
                with st.container(height=180, border=True):
                    for c in t.columns:
                        st.markdown(f"- `{c['name']}` *{c['type'] or 'TEXT'}*")
                
                if st.button("👀 Xem dữ liệu mẫu (5 dòng)", use_container_width=True):
                    try:
                        db_sidebar = get_db_manager(get_db_path())
                        df_preview = db_sidebar.execute_df(f'SELECT * FROM "{selected_table}" LIMIT 5')
                        st.dataframe(df_preview, use_container_width=True, hide_index=True)
                    except Exception as e:
                        st.error(f"Lỗi tải dữ liệu: {e}")
        except Exception as e:
            st.warning(f"Schema: {e}")

        st.divider()



        # ── Developer Tools ──────────────────────────────────────────────────────
        with st.expander("🛠️ Developer Tools", expanded=False):
            # Cache stats
            cache = get_semantic_cache()
            active_namespace = get_db_path()
            try:
                s = cache.stats(namespace=active_namespace)
            except TypeError:
                # Streamlit hot-reload có thể còn giữ singleton cache phiên bản cũ.
                s = cache.stats()
            diagnostic = (
                cache.last_lookup(namespace=active_namespace)
                if hasattr(cache, "last_lookup")
                else {}
            )
            st.subheader("📦 Semantic Cache")
            
            new_threshold = st.slider(
                "Ngưỡng tương đồng ngữ nghĩa (Cosine)",
                min_value=0.5, max_value=1.0, value=cache.threshold, step=0.01,
                help="Giảm để dễ Semantic Hit hơn. Exact Hit không phụ thuộc ngưỡng này."
            )
            if new_threshold != cache.threshold:
                cache.threshold = new_threshold

            new_jaccard_threshold = st.slider(
                "Ngưỡng trùng từ khóa (Jaccard)",
                min_value=0.0,
                max_value=1.0,
                value=cache.jaccard_threshold,
                step=0.01,
                help="Lớp bảo vệ ý định. Mặc định 0.65; giảm quá thấp có thể dùng nhầm cache.",
            )
            if new_jaccard_threshold != cache.jaccard_threshold:
                cache.jaccard_threshold = new_jaccard_threshold

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Size", f"{s['size']}/{s['max_size']}")
            col2.metric("Exact", s["exact_hits"])
            col3.metric("Semantic", s["semantic_hits"])
            col4.metric("Misses", s["misses"])
            st.metric("Hit rate", f"{s['hit_rate']:.1%}")

            if diagnostic:
                status_labels = {
                    "exact_hit": "Exact Hit",
                    "semantic_hit": "Semantic Hit",
                    "miss": "Miss",
                }
                reason_labels = {
                    "exact_key": "Câu hỏi trùng khớp sau khi chuẩn hóa",
                    "semantic_match": "Vượt qua Cosine, Jaccard và Critical Token Guard",
                    "no_entries": "Database này chưa có cache entry",
                    "embedding_unavailable": "Không tạo được embedding",
                    "embedding_backend_mismatch": "Backend/model embedding không tương thích",
                    "cosine_below_threshold": "Cosine thấp hơn ngưỡng",
                    "jaccard_below_threshold": "Jaccard thấp hơn ngưỡng",
                    "critical_token_mismatch": "Khác số lượng, toán tử hoặc từ khóa đầu/cuối/top",
                }
                status = diagnostic.get("status", "miss")
                message = status_labels.get(status, status)
                reason = reason_labels.get(
                    diagnostic.get("reason"),
                    diagnostic.get("reason", "Không xác định"),
                )
                if status.endswith("hit"):
                    st.success(f"Lần tra gần nhất: {message}")
                else:
                    st.warning(f"Lần tra gần nhất: {message}")
                st.caption(f"Lý do: {reason}")

                score_col1, score_col2, score_col3 = st.columns(3)
                cosine = diagnostic.get("cosine")
                jaccard = diagnostic.get("jaccard")
                score_col1.metric("Cosine", f"{cosine:.4f}" if cosine is not None else "N/A")
                score_col2.metric("Jaccard", f"{jaccard:.4f}" if jaccard is not None else "N/A")
                score_col3.metric("Lookup", f"{diagnostic.get('lookup_time_ms', 0):.2f} ms")

            if st.button("Xóa Cache", use_container_width=True, key="dev_clear_cache"):
                try:
                    cache.invalidate(namespace=active_namespace)
                except TypeError:
                    cache.invalidate()
                st.rerun()

            st.caption("Cache chỉ lưu trong bộ nhớ và sẽ reset khi ứng dụng khởi động lại.")

            st.divider()

            # API Status
            st.subheader("🔑 API")
            if config.GROQ_API_KEY or config.LLM_PROVIDER == "google":
                st.success(f"Provider: {config.LLM_PROVIDER.upper()}")
            else:
                st.error("No API key configured!")
            st.caption(f"Model Pro: `{config.LLM_MODEL_PRO}`")
            st.caption(f"Embedding: `{config.EMBEDDING_MODEL}`")
