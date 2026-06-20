# Multi-Agent Text-to-SQL System

> 📖 **Tài liệu chi tiết:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Giải thích toàn bộ code, kiến trúc, luồng dữ liệu.

Hệ thống **Multi-Agent Text-to-SQL** sử dụng LangGraph, cho phép truy vấn cơ sở dữ liệu bằng ngôn ngữ tự nhiên (tiếng Việt & tiếng Anh). Hệ thống hỗ trợ nạp linh hoạt (Onboard) nhiều cơ sở dữ liệu khác nhau thông qua Web UI và cơ chế Semantic Caching tiên tiến.

## 🌟 Kiến trúc

```text
User Question
     │
     ▼
┌─────────────────┐
│  Orchestrator   │ ← Phân tích intent, route tới agent phù hợp
└────────┬────────┘
         │
         ├─ [Simple] ──────→ SQL Generator → Validator → Executor → Formatter
         │
         └─ [Complex] ─────→ Query Planner → SQL Generator → Validator → Executor → Formatter
```

### 🧠 LangGraph Agents

| Agent | Vai trò | Công nghệ / LLM |
|-------|---------|-----------------|
| **Orchestrator** | Điều phối pipeline, phân tích độ phức tạp (intent) | Gemini 2.5 Flash |
| **Context Optimizer** | Tối ưu context window: Lọc bảng (Table Selector) và lọc cột (Column Pruner) | Khớp đồ thị (Graph Traversal) |
| **Query Planner** | Lên kế hoạch query phức tạp (JOIN, CTE, Aggregations) | Gemini 2.5 Flash |
| **SQL Generator** | Sinh SQL dựa trên Dynamic Schema và Kinh nghiệm RAG (Few-shots) | Gemini 2.5 Flash |
| **Validator** | Kiểm tra cú pháp SQL, chặn câu lệnh độc hại (DROP/DELETE) | Python `sqlite3` + Regex |
| **Executor** | Thực thi SQL an toàn trên database SQLite & in-memory caching | Jaccard + Cosine Cache |
| **Result Formatter**| Đóng gói kết quả thô thành JSON chuẩn, tự động nhận diện ý định vẽ Biểu đồ | Gemini 2.5 Flash |

## 🚀 Tính năng nổi bật

- ✅ **LLM Cốt lõi**: Hoạt động siêu tốc bằng mô hình **Gemini 1.5 Flash** (có hỗ trợ đổi sang Llama-3 qua Groq API).
- ✅ **Dynamic Few-Shot RAG**: Tự động sinh kinh nghiệm ảo (Synthetic Few-shots) bằng FAISS Vector Database ngay khi upload file `.sqlite` mới.
- ✅ **Cơ chế Jaccard + Semantic Cache**: Kết hợp so khớp từ vựng (Jaccard) và ngữ nghĩa (Cosine Similarity) để trả lời siêu tốc độ (0.5s) cho các câu hỏi lặp lại.
- ✅ **Giao diện Web Tương tác (Streamlit UI)**: Đẹp mắt, chuyên nghiệp. Hỗ trợ Upload file Database tự động học cấu trúc và sinh **Biểu đồ động Plotly Express**.
- ✅ **Execution Match Evaluation**: Phương pháp chấm điểm thực thi chạy đua trực tiếp song song (AI vs Gold SQL) kèm Trọng tài AI thông minh.

## ⚙️ Cài đặt

```bash
# 1. Clone / cd vào project
cd text_to_sql

# 2. Tạo virtual environment
python -m venv venv
source venv/Scripts/activate  # Windows
# source venv/bin/activate    # Linux/Mac

# 3. Cài dependencies
pip install -r requirements.txt

# 4. Cấu hình .env
cp .env.example .env
# Chỉnh sửa file .env và thêm:
# - GEMINI_API_KEY
```

## 🛠 Cách chạy Hệ thống

### 1. Giao diện Người dùng chính (Web UI)
Giao diện Chatbot truy vấn và hiển thị Biểu đồ (Dashboard):
```bash
streamlit run app/main.py
```
*(Mở `http://localhost:8501` trên trình duyệt)*

### 2. Giao diện Trọng tài Đánh giá (Evaluation Inspector UI)
Công cụ trực quan hóa (Side-by-side) dùng để chấm điểm trực tiếp kết quả của AI so với Đáp án chuẩn (Gold SQL):
```bash
streamlit run scripts/eval_inspector.py
```

### 3. Công cụ kỹ sư (Developer Tools)
- **Onboard hàng loạt DB**: Nạp toàn bộ folder Database vào hệ thống trong đêm.
  ```bash
  python scripts/bulk_onboard.py --dir data/spider/database --workers 8
  ```
- **Chấm điểm Batch (Evaluation)**: Chạy bài test 100 câu hỏi và tính tỷ lệ Accuracy.
  ```bash
  python test/evaluate.py
  ```

## 📂 Cấu trúc Project

```text
text_to_sql/
├── app/                 # Giao diện Web UI (Streamlit, Plotly Charts)
├── data/                # Chứa các file SQLite và JSON Test Sets
├── docs/                # Tài liệu thiết kế hệ thống
├── scripts/             # Các công cụ tiện ích (Bulk Onboard, Eval UI)
├── src/
│   ├── agents/          # Các Node của LangGraph (Planner, Generator, Validator...)
│   ├── db/              # Xử lý tương tác với SQLite
│   ├── rag/             # Hệ thống FAISS và VertexAI/Gemini Embeddings
│   └── config.py        # Quản lý cấu hình dự án
├── test/
│   └── evaluate.py      # Hệ thống chấm điểm Execution Match
└── .env                 # File biến môi trường
```
