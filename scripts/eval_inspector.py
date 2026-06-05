import streamlit as st
import json
import sqlite3
import pandas as pd
import sys
import time
from pathlib import Path

# Add project root and test directory to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "test"))

from src.graph import run_query
from evaluate import execution_match # Bypass Python's built-in 'test' module namespace issue

st.set_page_config(page_title="AI vs Gold SQL Inspector", page_icon="⚖️", layout="wide")

st.title("⚖️ Text-to-SQL Evaluation Inspector")
st.markdown("Giao diện đối chiếu trực tiếp giữa **Đáp án chuẩn (Gold SQL)** và **Hệ thống AI (Generated SQL)**. Dùng để demo độ thông minh của luồng Multi-Agent.")

# Load Test Dataset
@st.cache_data
def load_dataset():
    # Chúng ta ưu tiên load bộ Northwind Massive (100 câu)
    json_path = project_root / "data" / "northwind_massive_100.json"
    if not json_path.exists():
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

data = load_dataset()
if not data:
    st.error("Không tìm thấy file data/northwind_massive_100.json")
    st.stop()

# Handle different JSON structures (list of dicts vs dict with 'questions' key)
if isinstance(data, dict) and "questions" in data:
    data = data["questions"]

db_path = str(project_root / "data" / "northwind" / "northwind.sqlite")

# --- UI Header ---
option = st.selectbox(
    "🔎 Chọn câu hỏi cần đánh giá:", 
    [f"{d.get('id', i)}: {d['question']}" for i, d in enumerate(data)]
)

selected_id_str = option.split(":")[0]
try:
    selected = next(d for d in data if str(d.get("id")) == selected_id_str)
except StopIteration:
    selected = data[int(selected_id_str)] # Fallback if id is index

st.markdown("### 🗣️ Câu hỏi:")
st.info(selected["question"])

# --- Comparison Columns ---
col_gold, col_ai = st.columns(2)

gold_sql = selected.get("gold_sql", selected.get("query", ""))
gold_rows = []

with col_gold:
    st.markdown("### 🥇 Gold Standard (Đáp án chuẩn)")
    st.code(gold_sql, language="sql")
    
    try:
        conn = sqlite3.connect(db_path)
        gold_df = pd.read_sql_query(gold_sql, conn)
        conn.close()
        st.success(f"Thực thi thành công! (Trả về {len(gold_df)} dòng)")
        st.dataframe(gold_df)
        gold_rows = gold_df.values.tolist()
    except Exception as e:
        st.error(f"Lỗi khi chạy Gold SQL: {e}")

with col_ai:
    st.markdown("### 🤖 Hệ thống AI (Multi-Agent)")
    
    if st.button("🚀 Bắt AI làm bài (Run Query)", type="primary"):
        with st.spinner("LangGraph đang phân tích, RAG và sinh SQL..."):
            start_time = time.time()
            
            # Gọi trực tiếp bộ não AI
            result = run_query(selected["question"], db_path=db_path)
            
            latency = time.time() - start_time
            
        err = getattr(result, "execution_error", None) or getattr(result, "error", None)
        if err:
            st.error(f"AI chạy SQL bị lỗi: {err}")
        else:
            ai_sql = getattr(result, "generated_sql", "Không sinh được SQL")
            if not ai_sql: ai_sql = "Không sinh được SQL"
            st.code(ai_sql, language="sql")
            
            st.info(f"⏱️ Tốc độ phản hồi: {latency:.2f} giây")
            
            query_res = getattr(result, "query_result", None)
            if query_res and query_res.get("rows"):
                ai_df = pd.DataFrame(query_res["rows"], columns=query_res.get("columns", []))
                st.success(f"Thực thi thành công! (Trả về {len(ai_df)} dòng)")
                st.dataframe(ai_df)
                
                # Chấm điểm Execution Match
                st.divider()
                st.markdown("### 🏆 Trọng tài chấm điểm")
                is_match = execution_match(selected["question"], gold_sql, ai_sql, db_path)
                
                if is_match:
                    st.success("✅ **MATCH: TRUE!** Bảng dữ liệu AI sinh ra khớp 100% với Đáp án chuẩn. Quá xuất sắc!")
                else:
                    st.error("❌ **MATCH: FALSE!** Dữ liệu trả về bị lệch so với Đáp án chuẩn.")
            else:
                st.warning("AI đã chạy SQL nhưng bảng trả về bị trống (0 dòng).")
