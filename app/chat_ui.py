import streamlit as st
import pandas as pd
import time
from src.config import config
from src.graph import stream_query
from app.state import get_db_path
from app.charts import render_chart_plotly

def render_chat_history():
    # Nút xóa lịch sử trò chuyện
    col_spacer, col_clear = st.columns([5, 1])
    with col_clear:
        if st.button("🗑️ Xóa trò chuyện", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    # Hiển thị các tin nhắn cũ
    for i, msg in enumerate(st.session_state.messages):
        avatar = "👤" if msg["role"] == "user" else "✨"
        with st.chat_message(msg["role"], avatar=avatar):
            if msg["role"] == "user":
                # Thêm mỏ neo vô hình để CSS định vị và float right
                st.markdown(f'<span class="user-bubble-anchor"></span>{msg["content"]}', unsafe_allow_html=True)
            else:
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
                if msg.get("rows") is not None and msg.get("columns") is not None:
                    import pandas as pd
                    hist_df = pd.DataFrame(msg["rows"], columns=msg["columns"])
                    with st.expander("📊 Xem bảng dữ liệu"):
                        st.dataframe(hist_df, width="stretch", hide_index=True)
                    
                    # Render chart từ lịch sử
                    if msg.get("viz") and msg["viz"].get("recommended"):
                        viz = msg["viz"]
                        chart_type = viz.get("chart_type", "bar").lower()
                        with st.expander(f"📈 Biểu đồ trực quan ({chart_type.upper()})"):
                            render_chart_plotly(hist_df, chart_type)

def handle_user_input():
    if prompt := st.chat_input("Đặt câu hỏi về dữ liệu..."):
        if not config.GROQ_API_KEY and config.LLM_PROVIDER != "google":
            st.error("Vui lòng cấu hình API KEY trong file .env")
            st.stop()

        # Thêm tin nhắn của User vào lịch sử và hiển thị lên màn hình
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="👤"):
            st.markdown(f'<span class="user-bubble-anchor"></span>{prompt}', unsafe_allow_html=True)

        # Hiển thị UI của Assistant đang trả lời
        with st.chat_message("assistant", avatar="✨"):
            with st.status("✨ Đang suy luận và xử lý dữ liệu...", expanded=True) as status:
                st.write("Đang đọc schema và phân tích ngữ cảnh...")
                db_path = get_db_path()
                
                start_time = time.time()
                final_state = None
                try:
                    for step_output in stream_query(prompt, session_id=f"ui_{int(start_time)}", db_path=db_path):
                        for node_name, state in step_output.items():
                            if node_name == "router":
                                st.write("🔄 Đã định tuyến xong cơ sở dữ liệu")
                            elif node_name == "orchestrator":
                                st.write(f"🧠 Đã phân tích ý định...")
                            elif node_name == "query_planner":
                                st.write("📝 Đang lập kế hoạch truy vấn phức tạp (Query Plan)...")
                            elif node_name == "sql_generator":
                                st.write("⚡ Đang sinh câu lệnh SQL...")
                            elif node_name == "validator":
                                st.write("🔍 Đang kiểm tra tính an toàn của SQL (Validate)...")
                            elif node_name == "executor":
                                st.write("🚀 Đang thực thi SQL trên Database...")
                            elif node_name == "result_formatter":
                                st.write("✍️ Đang tổng hợp câu trả lời...")
                            final_state = state
                except Exception as e:
                    st.error(f"Lỗi hệ thống: {e}")
                    st.stop()
                
                from src.agents.state import AgentState
                if isinstance(final_state, dict):
                    result = AgentState(**final_state)
                else:
                    result = final_state
                    
                elapsed = time.time() - start_time
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
                        st.dataframe(df, width="stretch", hide_index=True)
                        
                    # Show Metrics (Hidden in expander for end users)
                    confidence = getattr(result, 'sql_confidence', 0.0) * 100
                    with st.expander("🛠️ Xem thông số thực thi (Developer)"):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("⏱️ Thời gian", f"{(time.time() - start_time):.1f}s")
                        col2.metric("🎯 Độ tự tin (AI)", f"{confidence:.0f}%")
                        col3.metric("📊 Dữ liệu trả về", f"{len(df)} dòng")
                    
                    # Tự động vẽ biểu đồ Plotly Express
                    if viz.get("recommended") and viz.get("chart_type", "table").lower() != "table":
                        chart_type = viz.get("chart_type", "bar").lower()
                        if len(df.columns) >= 2 or (len(df.columns) == 1 and len(df) > 1):
                            with st.expander(f"📈 Biểu đồ trực quan ({chart_type.upper()})"):
                                render_chart_plotly(df, chart_type)
                except Exception as e:
                    st.warning(f"Không thể hiển thị bảng: {e}")

            # Lưu lại câu trả lời vào lịch sử
            msg_data = {
                "role": "assistant", 
                "content": answer_content,
                "sql": sql,
                "viz": viz,
                "user_prompt": prompt,
                "is_trained": False
            }
            if hasattr(result, 'query_result') and result.query_result and result.query_result.get("rows"):
                msg_data["rows"] = result.query_result["rows"]
                msg_data["columns"] = result.query_result["columns"]
                
            st.session_state.messages.append(msg_data)

            # Tự động trích xuất tiêu đề từ câu hỏi đầu tiên
            title = st.session_state.messages[0]["content"] if st.session_state.messages else "Trò chuyện mới"
            
            # Lưu session vào ổ cứng
            from app.history_manager import save_session
            save_session(st.session_state.session_id, title, st.session_state.messages)
