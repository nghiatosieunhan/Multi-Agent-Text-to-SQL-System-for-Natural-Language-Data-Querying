import streamlit as st
import json
import sqlite3
import pandas as pd
import sys
import os
from pathlib import Path

# Add project root to sys.path so we can import from app
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from app.charts import render_chart_plotly
from src.config import config

st.set_page_config(page_title="Gold SQL Charts Viewer", page_icon="📊", layout="wide")

st.title("📊 Gold SQL Charts Viewer")
st.markdown("Xem trước Bảng dữ liệu và Biểu đồ được sinh ra trực tiếp từ các câu lệnh **Gold SQL** (đáp án chuẩn) trong file `chinook_charts.json`.")

@st.cache_data
def load_data():
    json_path = project_root / "data" / "chinook_charts.json"
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

data = load_data()
# Trỏ thẳng đến Database Chinook Tiếng Anh gốc (vì Gold SQL viết theo bảng Tiếng Anh)
db_path = project_root / "data" / "chinook" / "Chinook_Sqlite.sqlite"

try:
    conn = sqlite3.connect(db_path)
    
    # Dropdown to select question
    option = st.selectbox(
        "🔎 Chọn câu hỏi (Gold SQL):", 
        [f"{d['id']}: {d['question']}" for d in data]
    )
    
    selected_id = option.split(":")[0]
    selected = next(d for d in data if d["id"] == selected_id)

    st.markdown("**Câu hỏi:**")
    st.info(selected["question"])
    
    st.markdown("**Gold SQL:**")
    st.code(selected["query"], language="sql")

    # Detect chart type from question keywords
    chart_type = "bar" # Default
    q_lower = selected["question"].lower()
    if "tròn" in q_lower or "pie" in q_lower or "tỷ trọng" in q_lower or "tỷ lệ" in q_lower:
        chart_type = "pie"
    elif "đường" in q_lower or "xu hướng" in q_lower:
        chart_type = "line"
    elif "vùng" in q_lower or "area" in q_lower:
        chart_type = "area"

    # Execute Gold SQL
    df = pd.read_sql_query(selected["query"], conn)

    st.divider()
    col1, col2 = st.columns([1, 1.5])
    
    with col1:
        st.subheader("Bảng Dữ Liệu")
        st.dataframe(df, use_container_width=True, hide_index=True)
        
    with col2:
        st.subheader(f"Biểu Đồ (Loại: {chart_type.upper()})")
        render_chart_plotly(df, chart_type)

except Exception as e:
    st.error(f"Lỗi: {e}")
finally:
    if 'conn' in locals():
        conn.close()
