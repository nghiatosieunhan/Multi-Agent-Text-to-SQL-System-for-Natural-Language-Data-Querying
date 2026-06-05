import streamlit as st

def load_css():
    st.markdown("""
    <style>
    /* 1. Nhúng Font chữ chuyên nghiệp (Inter) */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif !important;
    }

    /* 2. Tinh chỉnh Header / Tiêu đề */
    h1 { font-size: 24px !important; font-weight: 700 !important; color: #0F172A !important;}
    h2 { font-size: 20px !important; font-weight: 600 !important;}
    h3 { font-size: 16px !important; font-weight: 600 !important;}

    /* 3. Tinh chỉnh khung tin nhắn Chat (Chat Bubbles) */
    .stChatMessage {
        padding: 1.2rem 1.5rem !important;
        border-radius: 12px !important;
        margin-bottom: 1.5rem !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
        max-width: 85% !important; /* Giới hạn chiều rộng tin nhắn */
        clear: both;
    }

    /* Tin nhắn của USER (Nền Dark Navy, chữ Trắng, bo góc lề phải) */
    div[data-testid="stChatMessage"]:has(.user-bubble-anchor) {
        background-color: #0F172A !important; 
        float: right !important;
        border-bottom-right-radius: 4px !important;
        flex-direction: row-reverse;
        text-align: right;
    }
    div[data-testid="stChatMessage"]:has(.user-bubble-anchor) p, 
    div[data-testid="stChatMessage"]:has(.user-bubble-anchor) div,
    div[data-testid="stChatMessage"]:has(.user-bubble-anchor) span {
        color: #FFFFFF !important; 
    }

    /* Tin nhắn của ASSISTANT (Nền Trắng tinh, viền nhẹ, bo góc lề trái) */
    div[data-testid="stChatMessage"]:not(:has(.user-bubble-anchor)) {
        background-color: #FFFFFF !important; 
        border: 1px solid #E2E8F0 !important;
        float: left !important;
        border-bottom-left-radius: 4px !important;
    }

    /* Khắc phục lỗi hiển thị cấu trúc sau khi thả trôi (float) */
    [data-testid="stChatMessageContainer"]::after {
        content: "";
        clear: both;
        display: table;
    }

    /* 4. Tinh chỉnh khung nhập liệu (Floating Chat Input) */
    [data-testid="stChatInput"] {
        background-color: transparent !important;
    }
    [data-testid="stChatInput"] > div {
        background-color: #FFFFFF !important;
        border-radius: 16px !important;
        border: 1px solid #CBD5E1 !important;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05) !important;
        padding: 0.2rem 1rem !important;
    }

    /* 5. Tinh chỉnh Expander (Nơi xem SQL và Bảng Data) */
    .streamlit-expanderHeader {
        background-color: #F8F9FA !important;
        border-radius: 8px !important;
        border: 1px solid #E2E8F0 !important;
        font-weight: 600 !important;
    }
    .streamlit-expanderContent {
        border: 1px solid #E2E8F0 !important;
        border-top: none !important;
        border-bottom-left-radius: 8px !important;
        border-bottom-right-radius: 8px !important;
        background-color: #FFFFFF !important;
    }

    /* 6. Code Block (SQL) */
    .stCodeBlock {
        border-radius: 8px !important;
        overflow: hidden !important;
    }

    /* 7. Tinh chỉnh nút bấm Xóa Trò Chuyện */
    button[kind="secondary"] {
        color: #64748B !important;
        background-color: #FFFFFF !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 8px !important;
    }
    button[kind="secondary"]:hover {
        color: #EF4444 !important;
        border-color: #EF4444 !important;
        background-color: #FEF2F2 !important;
    }

    /* 8. Ẩn menu thừa của Streamlit */
    #MainMenu, footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)