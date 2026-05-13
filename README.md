# Multi-Agent Text-to-SQL System

> 📖 **Tài liệu chi tiết:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Giải thích toàn bộ code, kiến trúc, luồng dữ liệu.

Hệ thống **Multi-Agent Text-to-SQL** sử dụng LangGraph, cho phép truy vấn cơ sở dữ liệu bằng ngôn ngữ tự nhiên (tiếng Việt & tiếng Anh). Hệ thống hỗ trợ nạp linh hoạt nhiều cơ sở dữ liệu (Chinook, BIRD, Spider...) thông qua cơ chế Registry.

## Kiến trúc

```text
User Question
     │
     ▼
┌─────────────────┐
│   Route Node    │ ← Xác định Database mục tiêu từ câu hỏi
└────────┬────────┘
         ▼
┌─────────────────┐
│  Orchestrator   │ ← Phân tích intent, route tới agent phù hợp
└────────┬────────┘
         │
         ├─ [Simple] ──────→ SQL Generator → Validator → Executor → Formatter
         │
         └─ [Complex] ─────→ Query Planner → SQL Generator → Validator → Executor → Formatter
```

### LangGraph Agents

| Agent | Vai trò | Trạng thái / Model |
|-------|---------|------------|
| **Route Node** | Xác định đúng cơ sở dữ liệu cần truy vấn từ `registry.json` | Llama 3.3 70B (Groq) |
| **Orchestrator** | Điều phối pipeline, phân tích độ phức tạp (intent) | Llama 3.3 70B (Groq) |
| **QueryPlanner** | Lên kế hoạch query phức tạp (JOIN, CTE, Aggregations) | Llama 3.3 70B (Groq) |
| **SQLGenerator** | Sinh SQL dựa trên schema context và kinh nghiệm (few-shots) | Llama 3.3 70B (Groq) |
| **Validator** | Kiểm tra cú pháp SQL, chặn câu lệnh độc hại (DROP/DELETE) | Rule-based & DB Native |
| **Executor** | Thực thi SQL an toàn trên database SQLite | Python `sqlite3` |
| **ResultFormatter**| Format data thô thành câu trả lời tự nhiên cho người dùng | Llama 3.3 70B (Groq) |

*Ngoài ra hệ thống còn tích hợp `table_selector.py`, `column_pruner.py` và `auto_fewshot.py` giúp tối ưu hóa Context Window để tiết kiệm token cho LLM.*

## Tính năng nổi bật

- ✅ **Dynamic Multi-DB**: KHÔNG sử dụng prompt hardcode. Tự động đọc và sinh metadata (introspect) cho bất kỳ DB SQLite nào.
- ✅ **Groq LLM**: Sử dụng model `llama-3.3-70b-versatile` qua Langchain-Groq API cho tốc độ phản hồi siêu tốc.
- ✅ **Auto Few-shot**: Tự động sinh kinh nghiệm ảo (synthetic few-shots) bằng FAISS và Google Cloud Vertex AI Embeddings (tốc độ cao, không bị giới hạn Rate Limit như Mistral) để cải thiện độ chính xác khi sinh SQL.
- ✅ **Cơ chế tự sửa lỗi**: LLM có khả năng nhận phản hồi lỗi từ Validator/Executor và tự viết lại câu lệnh SQL.
- ✅ **Giao diện hiện đại**: Sử dụng Streamlit với CSS tuỳ chỉnh đem lại trải nghiệm tương đương các chatbot AI cao cấp.

## Cài đặt

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
# - GROQ_API_KEY
# - GOOGLE_CLOUD_PROJECT
# - GOOGLE_CLOUD_LOCATION

# 5. Xác thực Google Cloud (Để dùng Embeddings)
gcloud auth application-default login
```

## Cấu trúc Project chính

```text
text_to_sql/
├── src/
│   ├── agents/          # LangGraph agents
│   │   ├── auto_fewshot.py
│   │   ├── column_pruner.py
│   │   ├── executor.py
│   │   ├── groq_llm.py   # Groq LLM client wrapper (Thay cho Gemini)
│   │   ├── onboard.py    # Multi-DB onboarding & semantic cache
│   │   ├── orchestrator.py
│   │   ├── query_planner.py
│   │   ├── result_formatter.py
│   │   ├── route_node.py
│   │   ├── sql_generator.py
│   │   ├── state.py      # LangGraph Pydantic State schema
│   │   ├── table_selector.py
│   │   └── validator.py
│   ├── db/              # Database interaction & utils
│   ├── rag/             # RAG (FAISS/VertexAI Embeddings)
│   └── config.py        # Quản lý cấu hình
├── registry.json       # Danh sách các Databases đang được quản lý
├── main.py             # CLI entry point
├── app.py              # Streamlit Web UI chính
└── .env                # Biến môi trường
```

## Chạy hệ thống

### Web UI (Streamlit)
```bash
streamlit run app.py
```
*Mở `http://localhost:8501` trên trình duyệt để trải nghiệm.*

### Đánh giá (Evaluation)
Để kiểm tra độ chính xác trên bộ dataset (BIRD, Spider, Chinook):
```bash
python evaluate.py
```
