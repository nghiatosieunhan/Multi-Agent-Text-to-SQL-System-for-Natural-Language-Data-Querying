import streamlit as st
import pandas as pd
import time
import json
import re
from src.config import config
from src.graph import stream_query
from app.state import get_db_path
from app.charts import render_chart_plotly


def _parse_json_text(value):
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    first_brace = text.find("{")
    if first_brace < 0:
        return None

    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[first_brace:].strip())
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _answer_from_dict(data: dict) -> str:
    parts = []

    summary = _clean_chat_text(data.get("summary"))
    detailed_answer = _clean_chat_text(data.get("detailed_answer"))
    if summary:
        parts.append(f"**{summary}**")
    if detailed_answer and detailed_answer != summary:
        parts.append(detailed_answer)

    insights = data.get("insights")
    if isinstance(insights, list):
        clean_insights = [_clean_chat_text(insight) for insight in insights]
        clean_insights = [insight for insight in clean_insights if insight]
        if clean_insights:
            parts.append("💡 **Insights:**\n" + "\n".join(f"- {insight}" for insight in clean_insights))

    return "\n\n".join(parts)


def _clean_chat_text(value):
    """Return only user-facing text, never raw result objects."""
    if not isinstance(value, str):
        return ""

    text = value.strip()
    if not text:
        return ""

    parsed = _parse_json_text(text)
    if parsed and any(key in parsed for key in ("summary", "detailed_answer", "insights")):
        return _answer_from_dict(parsed)

    raw_markers = ("'rows':", '"rows":', "'columns':", '"columns":', "'sql':", '"sql":')
    if (text.startswith("{") or text.startswith("[")) and any(marker in text for marker in raw_markers):
        return ""
    if any(marker in text for marker in raw_markers) and len(text) > 300:
        return ""

    return text


def _build_answer_content(result) -> str:
    if hasattr(result, 'error') and result.error and not (hasattr(result, 'formatted_answer') and result.formatted_answer):
        return f"❌ **Lỗi:** {result.error}"

    if hasattr(result, 'formatted_answer') and result.formatted_answer:
        fa = result.formatted_answer
        for key in ("chat_response", "detailed_answer", "summary"):
            parsed = _parse_json_text(fa.get(key))
            if parsed and any(field in parsed for field in ("summary", "detailed_answer", "insights")):
                fa = {**fa, **parsed}
                break

        if "chat_response" in fa:
            chat_response = _clean_chat_text(fa.get("chat_response"))
            if chat_response:
                return chat_response

        answer = _answer_from_dict(fa)
        if answer:
            return answer

    if hasattr(result, 'query_result') and result.query_result:
        row_count = result.query_result.get('row_count', 0)
        if row_count:
            return f"✅ Truy vấn đã trả về {row_count} dòng dữ liệu. Bạn có thể xem chi tiết trong bảng bên dưới."
        return "✅ Truy vấn chạy thành công nhưng không tìm thấy dữ liệu phù hợp."

    return "Xin lỗi, tôi chưa tạo được câu trả lời phù hợp cho truy vấn này."


def _get_result_payload(result) -> dict:
    if getattr(result, "query_result", None):
        return result.query_result
    if getattr(result, "cached_result", None):
        return result.cached_result
    if getattr(result, "formatted_answer", None):
        formatted = result.formatted_answer
        if formatted.get("rows") is not None and formatted.get("columns") is not None:
            return {
                "rows": formatted.get("rows", []),
                "columns": formatted.get("columns", []),
                "row_count": formatted.get("row_count", len(formatted.get("rows", []))),
            }
    return {}


def render_chat_history():
    # Các nút công cụ đã được chuyển sang app/main.py để nằm trên cùng

    # Hiển thị các tin nhắn cũ
    for i, msg in enumerate(st.session_state.messages):
        avatar = "👤" if msg["role"] == "user" else "✨"
        with st.chat_message(msg["role"], avatar=avatar):
            if msg["role"] == "user":
                # Thêm mỏ neo vô hình để CSS định vị và float right
                st.markdown(f'<span class="user-bubble-anchor"></span>{msg["content"]}', unsafe_allow_html=True)
            else:
                content = _clean_chat_text(msg.get("content"))
                if not content and msg.get("rows") is not None:
                    content = f"Truy vấn đã trả về {len(msg.get('rows', []))} dòng dữ liệu. Bạn có thể xem chi tiết trong bảng bên dưới."
                st.markdown(content or "Kết quả đã được lưu trong các phần bên dưới.")
            
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
                                    from pathlib import Path
                                    db_name = Path(get_db_path()).stem
                                    retriever.add_single_example(msg["user_prompt"], msg["sql"], dataset_type=db_name)
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
    # Lấy mode_value từ selectbox ở góc phải trên (luôn là deep vì Pro là coming soon)
    mode_value = "deep" 

    prompt = st.chat_input("Đặt câu hỏi về dữ liệu...")
    if st.session_state.get("pending_suggestion"):
        prompt = st.session_state.pending_suggestion
        del st.session_state.pending_suggestion
        
    if prompt:
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
                final_state_dict = {}
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
                            
                            # Tích lũy (accumulate) state từ các node
                            if isinstance(state, dict):
                                final_state_dict.update(state)
                            elif hasattr(state, "__dict__"):
                                final_state_dict.update(state.__dict__)
                except Exception as e:
                    st.error(f"Lỗi hệ thống: {e}")
                    st.stop()
                
                from src.agents.state import AgentState
                result = AgentState(**final_state_dict)
                    
                elapsed = time.time() - start_time
                status.update(label=f"Hoàn tất xử lý trong {elapsed:.2f}s", state="complete", expanded=False)
                
            df = None
            sql = result.generated_sql if hasattr(result, 'generated_sql') else None
            result_payload = _get_result_payload(result)
            
            answer_content = _build_answer_content(result)

            # In câu trả lời chính
            st.markdown(answer_content)
            
            # Tạo Expander cho SQL
            if sql:
                with st.expander("🔍 Xem câu lệnh SQL"):
                    st.code(sql, language="sql")
            
            # Lấy cấu hình biểu đồ và gợi ý từ LLM
            viz = {}
            suggestions = []
            if hasattr(result, 'formatted_answer') and result.formatted_answer:
                viz = result.formatted_answer.get("visualization", {})
                suggestions = result.formatted_answer.get("suggestions", [])
                
            # Tạo Expander cho bảng dữ liệu Pandas và vẽ biểu đồ
            if result_payload.get("rows"):
                rows = result_payload["rows"]
                cols = result_payload.get("columns", [])
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
                    
                    # Tự động vẽ biểu đồ
                    chart_type = None
                    if mode_value == "fast":
                        if len(df.columns) >= 2:
                            chart_type = "bar" # Mặc định bar chart cho fast mode
                    elif viz.get("recommended") and viz.get("chart_type", "table").lower() != "table":
                        chart_type = viz.get("chart_type", "bar").lower()

                    if chart_type and (len(df.columns) >= 2 or (len(df.columns) == 1 and len(df) > 1)):
                        with st.expander(f"📈 Biểu đồ trực quan ({chart_type.upper()})"):
                            render_chart_plotly(df, chart_type)
                except Exception as e:
                    st.warning(f"Không thể hiển thị bảng: {e}")

            # Render suggestions (Chỉ bật khi ở chế độ Pro / Chuyên sâu)
            is_pro_mode = "Pro" in st.session_state.get("analysis_mode_selector", "")
            if suggestions and is_pro_mode:
                st.write("💡 *Có thể bạn quan tâm:*")
                cols = st.columns(len(suggestions))
                for idx, suggestion_text in enumerate(suggestions):
                    with cols[idx]:
                        if st.button(suggestion_text, key=f"suggest_{int(time.time())}_{idx}", use_container_width=True):
                            st.session_state.pending_suggestion = suggestion_text
                            st.rerun()

            # Lưu lại câu trả lời vào lịch sử
            msg_data = {
                "role": "assistant", 
                "content": answer_content,
                "sql": sql,
                "viz": viz,
                "suggestions": suggestions,
                "user_prompt": prompt,
                "is_trained": False
            }
            if result_payload.get("rows"):
                msg_data["rows"] = result_payload["rows"]
                msg_data["columns"] = result_payload.get("columns", [])
                
            st.session_state.messages.append(msg_data)

            # Tự động trích xuất tiêu đề từ câu hỏi đầu tiên
            title = st.session_state.messages[0]["content"] if st.session_state.messages else "Trò chuyện mới"
            
            # Lưu session vào ổ cứng
            from app.history_manager import save_session
            save_session(st.session_state.session_id, title, st.session_state.messages)
