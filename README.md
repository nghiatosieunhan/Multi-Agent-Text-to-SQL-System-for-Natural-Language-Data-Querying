# Multi-Agent Text-to-SQL System

> 📖 **Tài liệu chi tiết:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Giải thích toàn bộ code, kiến trúc, luồng dữ liệu.

Hệ thống **Multi-Agent Text-to-SQL** sử dụng LangGraph, cho phép truy vấn database bằng ngôn ngữ tự nhiên (tiếng Việt).

## Kiến trúc

```
User Question
     │
     ▼
┌─────────────────┐
│  Orchestrator   │ ← Phân tích intent, route agents
└────────┬────────┘
         │
         ├─ [Cache Hit?] ─→ Result Formatter → Answer
         │
         ├─ [Simple] ──────→ SQL Generator → Executor → Formatter
         │
         └─ [Complex] ─────→ Query Planner → SQL Generator → Executor → Formatter
```

### Agents

| Agent | Vai trò | Model |
|-------|---------|-------|
| **Orchestrator** | Điều phối pipeline, phân tích intent | `llama-3.3-70b-versatile` |
| **QueryPlanner** | Lên kế hoạch query phức tạp | `qwen3-32b` |
| **SQLGenerator** | Sinh SQL từ câu hỏi + schema | `llama-3.3-70b-versatile` |
| **Executor** | Thực thi SQL trên SQLite | — |
| **ResultFormatter** | Format kết quả + visualization | `llama-3.1-8b-instant` |

## Tính năng

- ✅ Text-to-SQL bằng tiếng Việt
- ✅ Multi-agent pipeline với LangGraph
- ✅ Structured prompting + query templates
- ✅ Semantic caching (Mistral embedding)
- ✅ Schema RAG (ChromaDB)
- ✅ Data pipeline (CSV, JSON, Web crawl → SQLite)
- ✅ Data visualization (bar, line, pie charts)
- ✅ Safety: chỉ SELECT queries được phép

## Cài đặt

```bash
# 1. Clone / cd vào project
cd i:/AI/text_to_sql

# 2. Tạo virtual environment
python -m venv venv
source venv/Scripts/activate  # Windows
# source venv/bin/activate   # Linux/Mac

# 3. Cài dependencies
pip install -r requirements.txt

# 4. Copy và chỉnh sửa .env
cp .env.example .env
# Thêm GROQ_API_KEY_1 và MISTRAL_API_KEY vào .env
```

## API Keys

Lấy keys tại:
- **Groq**: https://console.groq.com/keys
- **Mistral**: https://console.mistral.ai/

## Chạy hệ thống

### 1. Khởi tạo (một lần)
```bash
python main.py --init
```
Lệnh này:
- Tạo database SQLite với sample data (5 bảng kinh doanh)
- Index schema vào ChromaDB
- Kiểm tra API keys

### 2. Chế độ tương tác (CLI)
```bash
python main.py
```
Chat liên tục với hệ thống. Gõ `exit` để thoát, `clear` để xóa cache, `stats` để xem cache stats.

### 3. Single query
```bash
python main.py --query "Tổng số đơn hàng theo khách hàng"
```

### 4. Batch queries
```bash
python main.py --batch queries.txt
```

### 5. Web UI (Streamlit)
```bash
streamlit run app.py
```
Mở http://localhost:8501

## Cấu trúc Project

```
text_to_sql/
├── src/
│   ├── agents/          # LangGraph agents + prompts
│   │   ├── orchestrator.py
│   │   ├── query_planner.py
│   │   ├── sql_generator.py
│   │   ├── executor.py
│   │   ├── result_formatter.py
│   │   ├── state.py      # LangGraph state schema
│   │   ├── groq_llm.py   # Groq LLM wrapper
│   │   └── prompts.py    # System prompts
│   ├── db/              # Database management
│   │   ├── database.py   # SQLite manager
│   │   └── data_pipeline.py  # Crawl→Clean→Load
│   ├── rag/             # RAG components
│   │   ├── embedder.py    # Mistral batch embedding
│   │   ├── chroma_store.py # ChromaDB vector store
│   │   └── schema_indexer.py
│   ├── memory/          # Semantic cache
│   │   └── semantic_cache.py
│   ├── tools/           # Visualization
│   │   └── visualizer.py
│   ├── config.py        # Cấu hình từ .env
│   ├── schema.py        # Pydantic models
│   └── graph.py         # LangGraph workflow
├── main.py             # CLI entry point
├── app.py              # Streamlit web UI
├── requirements.txt
├── .env.example
└── README.md
```

## Sample Data

Hệ thống tự động tạo 5 bảng demo:

| Bảng | Mô tả | Rows |
|------|-------|------|
| `products` | Sản phẩm công nghệ | 20 |
| `customers` | Khách hàng | 15 |
| `orders` | Đơn hàng | 50 |
| `suppliers` | Nhà cung cấp | 9 |
| `reviews` | Đánh giá sản phẩm | 30 |

## Test

```bash
pytest tests/ -v
```

## Giới hạn Free Tier

| API | Limit | Strategy |
|-----|-------|----------|
| **Mistral** (embed) | 2 RPM | Batch + ChromaDB cache |
| **Groq** (LLM) | 6K-30K tokens/phút | Sequential flow, model tiering |

## Ví dụ Queries

```sql
-- Q: Tổng số sản phẩm theo danh mục?
SELECT category, COUNT(*) as count FROM products GROUP BY category;

-- Q: Top 5 khách hàng có nhiều đơn hàng nhất?
SELECT c.customer_name, COUNT(o.order_id) as order_count
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_name
ORDER BY order_count DESC LIMIT 5;

-- Q: Sản phẩm nào được đánh giá cao nhất?
SELECT p.product_name, AVG(r.rating) as avg_rating
FROM products p
JOIN reviews r ON p.product_id = r.product_id
GROUP BY p.product_name
ORDER BY avg_rating DESC LIMIT 3;
```
