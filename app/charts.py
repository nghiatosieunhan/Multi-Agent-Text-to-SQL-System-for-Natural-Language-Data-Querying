import streamlit as st
import pandas as pd
import plotly.express as px

def render_chart_plotly(df, chart_type):
    if df.empty:
        st.caption("Dữ liệu trống.")
        return
        
    df_numeric = df.copy()
    for col in df_numeric.columns:
        df_numeric[col] = pd.to_numeric(df_numeric[col], errors='ignore')
        
    numeric_cols = [c for c in df_numeric.columns if pd.api.types.is_numeric_dtype(df_numeric[c])]
    string_cols = [c for c in df_numeric.columns if not pd.api.types.is_numeric_dtype(df_numeric[c])]
    
    if string_cols and numeric_cols:
        x_col = string_cols[0]
        y_col = numeric_cols[0]
    elif len(numeric_cols) >= 2:
        x_col = numeric_cols[0]
        y_col = numeric_cols[1]
    else:
        st.caption("Không đủ dữ liệu dạng số để vẽ biểu đồ.")
        return

    try:
        if chart_type in ["pie", "đồ thị tròn"]:
            fig = px.pie(df_numeric, names=x_col, values=y_col, hole=0.3)
        elif chart_type in ["line", "đường"]:
            fig = px.line(df_numeric, x=x_col, y=y_col, markers=True)
        elif chart_type in ["area", "vùng"]:
            fig = px.area(df_numeric, x=x_col, y=y_col)
        else:
            fig = px.bar(df_numeric, x=x_col, y=y_col, color=x_col)
            
        fig.update_layout(margin=dict(l=20, r=20, t=30, b=20), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    except Exception as e:
        st.caption(f"Lỗi vẽ biểu đồ: {e}")
