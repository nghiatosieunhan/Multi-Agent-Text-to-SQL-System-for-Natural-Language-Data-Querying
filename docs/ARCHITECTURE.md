# 📖 Tài Liệu Kiến Trúc — Multi-Agent Text-to-SQL System

> **Mục tiêu:** Cho phép người dùng truy vấn **bất kỳ** cơ sở dữ liệu SQLite nào bằng ngôn ngữ tự nhiên (tiếng Việt) — không chỉ Chinook.
> **AI Stack:** 100% Google Gemini (LLM + Embedding), LangGraph, SQLite.
> **Multi-DB:** Onboarding tự động, dynamic validation, schema cache.

---

## Mục Lục

1. [Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống)
2. [Kiến Trúc Multi-Agent LangGraph](#2-kiến-trúc-multi-agent-langgraph)
3. [Chi Tiết Từng Agent](#3-chi-tiết-từng-agent)
4. [Multi-Database System — Onboarding & Validation](#4-multi-database-system--onboarding--validation)
5. [RAG & Embedding](#5-rag--embedding)
6. [Semantic Cache](#6-semantic-cache)
7. [Evaluation & Checkpointing](#7-evaluation--checkpointing)
8. [Security & Safety](#8-security--safety)
9. [Cấu Hình](#9-cấu-hình)
10. [Cấu Trúc File](#10-cấu-trúc-file)
11. [Hướng Dẫn Chạy](#11-hướng-dẫn-chạy)

---

## 1. Tổng Quan Hệ Thống

### 1.1 Mục tiêu

| Mục tiêu | Mô tả |
|-----------|--------|
| Text-to-SQL | Chuyển câu hỏi tiếng Việt → SQL SELECT query |
| Multi-Database | Hỗ trợ **bất kỳ** SQLite file nào, không chỉ Chinook |
| Multi-Agent | 6 agents phối hợp theo pipeline, mỗi agent một vai trò riêng |
| Dynamic Validation | Kiểm tra SQL bằng cách gọi DB thật — verify table/column tồn tại |
| Onboarding | Tự động introspect + sinh semantic descriptions khi thêm DB mới |
| RAG | Truy xuất schema context dựa trên Gemini embedding |
| Semantic Cache | Cache kết quả ở executor level, cosine similarity, LRU eviction |

### 1.2 Tech Stack

```
LLM:           Google Gemini (models/gemini-2.5-flash-lite) — single model
Embedding:     Google Gemini (models/gemini-embedding-001) — TF-IDF fallback
Workflow:      LangGraph (StateGraph, conditional edges)
Database:      SQLite (any .sqlite / .db file)
Cache:         In-memory LRU (OrderedDict), cosine similarity
Onboarding:    Automatic schema introspection + LLM semantic descriptions
Config:        .env + python-dotenv
```

### 1.3 Kiến trúc tổng quan

```
┌──────────────────────────────────────────────────────────────────┐
│                    User / evaluate.py / app.py                    │
│               (CLI: main.py  |  Web: Streamlit app.py)          │
└───────────────────────┬──────────────────────────────────────────┘
                        │ question
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│   LangGraph Workflow (src/graph.py) — Multi-Agent Pipeline        │
│                                                                  │
│   orchestrator (calls Table Selector & Column Pruner)            │
│         ↓                                                        │
│   [plan_needed?] → query_planner                                 │
│         └→ sql_generator                                         │
│                  ↓                                               │
│              validator (DYNAMIC VALIDATION)                      │
│                  ↓                                               │
│               executor (Semantic Cache)                          │
│                  ↓                                               │
│           result_formatter → END                                 │
└───────────────────────┬──────────────────────────────────────────┘
                        │
          ┌─────────────┼──────────────────┐
          ▼             ▼                  ▼
   ┌─────────────┐ ┌────────────────┐   ┌────────────────────┐
   │   SQLite     │ │ FAISS Unified  │   │ Semantic Cache     │
   │  (any .db)   │ │ (Few-Shot RAG) │   │  (in-memory LRU)   │
   └─────────────┘ └────────────────┘   └────────────────────┘
          │
          ▼
   ┌─────────────────────────────────────────┐
   │  schemas/{hash}.json                     │
   │  Semantic descriptions cache (per-DB)    │
   └─────────────────────────────────────────┘
```

### 1.4 Benchmark Results

| Dataset | Questions | PASS | Accuracy |
|---------|-----------|------|----------|
| `data/data.json` (50 câu test) | 50 | 50 | **100.0%** |
| Production eval (300 câu) | 300 | 298 | **99.3%** |

---

## 2. Kiến Trúc Multi-Agent LangGraph

### 2.1 AgentState Schema (`src/agents/state.py`)

```python
class AgentState(BaseModel):
    # Input
    user_question: str
    session_id: str

    # Multi-DB support
    current_db_path: str           # Đường dẫn SQLite file hiện tại
    current_db_schema: SchemaContext  # Schema context đã onboarded

    # Orchestrator
    intent_type: str          # simple|aggregate|join|complex|ambiguous
    intent_confidence: float  # 0.0–1.0
    plan_needed: bool         # True → query_planner, False → sql_generator

    # Cache
    cache_checked: bool
    cache_hit: bool
    cached_result: dict

    # Schema / RAG
    schema_context: str       # Dynamic — từ schema indexer
    tables_identified: list
    columns_identified: list

    # Query Planner
    plan: dict               # Execution plan

    # SQL Generator
    generated_sql: str
    sql_confidence: float
    generation_attempts: int

    # Executor
    query_result: dict        # {sql, columns, rows, row_count, execution_time_ms}
    execution_error: str

    # Formatter
    formatted_answer: dict   # {summary, detailed_answer, insights, visualization}

    # Pipeline control
    current_step: str        # "start" | "validated" | "execution_success" | ...
    next_agent: str          # "sql_generator" | "executor" | "error" | ...
    error: str
    retry_count: int
    max_retries: int = 2
```

### 2.2 Pipeline Flow

```
START
  │
  ▼
orchestrator
  ├─ get_schema_context_for_query()
  │    ├─ Table Selector Agent (Graph Traversal via FK)
  │    └─ Column Pruner Agent (Bảo toàn PK/FK)
  ├─ _safe_json_parse() — parse intent
  ├─ plan_needed = intent in (join, complex, cte, subquery, aggregate)
  └─ route: plan_needed? → query_planner : sql_generator
          │
          ▼
query_planner (chỉ chạy khi plan_needed=True)
  ├─ System prompt: dynamic schema từ state.schema_context
  └─ Output: execution plan (steps, tables_used, columns_used)
          │
          ▼
sql_generator (DYNAMIC — schema từ state.schema_context)
  ├─ SYSTEM_PROMPT_TEMPLATE = {schema} → injected at runtime
  ├─ _extract_sql() — JSON / ```sql fences / plain SELECT fallback
  ├─ _validate_dangerous() — chặn DROP/INSERT/UPDATE/DELETE
  ├─ _build_fallback_sql() — rule-based khi LLM fail
  └─ generated_sql → validator
          │
          ▼
validator (DYNAMIC VALIDATION — gọi DB thật)
  ├─ _fix_common_errors() — TOP→SELECT, LIMIT 1st→LIMIT N, \'→''
  ├─ _validate_tables() — db.table_exists() (case-insensitive)
  ├─ _validate_column_refs() — db.column_exists() (case-sensitive)
  ├─ _check_hint_patterns() — detect common LLM mistakes
  ├─ hard_validate() — SELECT/WITH + dangerous keywords + dynamic checks
  └─ fail → retry sql_generator với error context; pass → executor
          │
          ▼
executor
  ├─ _fix_common_errors() — ÁP DỤNG TRƯỚC KHI exec
  ├─ db.execute_query() — thực thi SQL
  ├─ cache.put() — lưu vào semantic cache
  └─ fail → retry sql_generator (tối đa max_retries); pass → formatter
          │
          ▼
result_formatter
  └─ format kết quả thành câu trả lời tự nhiên + visualization đề xuất
          │
          ▼
        END
```

### 2.3 DB Switch Detection (`_ensure_db()`)

Khi `db_path` thay đổi trong `run_query()`:

```python
if resolved != _current_db_path:
    # 1. Init/reinit DB manager
    get_db_manager(resolved)

    # 2. Rebuild schema index với semantic descriptions
    schema, descriptions = get_current_db_schema(resolved)
    rebuild_schema_index(db, db_path=resolved)

    # 3. Cập nhật global tracker
    _current_db_path = resolved
```

---

## 3. Chi Tiết Từng Agent

### Agent 1: Orchestrator (`src/agents/orchestrator.py`)

**Vai trò:** "Bộ não" trung tâm — phân tích intent, quyết định route.

```python
def orchestrator_node(state):
    # 1. Semantic cache lookup — DISABLED ở orchestrator level
    #    (Lý do: "số lượng album" → similarity match "danh sách album"
    #     → trả về SQL sai — cache poisoning)
    state.cache_checked = True

    # 2. Retrieve schema context từ schema_indexer (DYNAMIC)
    schema_context = get_schema_context_for_query(question, top_k=6)
    state.schema_context = schema_context

    # 3. Gọi Gemini LLM phân tích intent
    decision = _safe_json_parse(raw_response)

    state.intent_type = decision["intent_type"]
    state.plan_needed = intent in ("join", "complex", "cte", "subquery", "aggregate")
    state.next_agent = "query_planner" if plan_needed else "sql_generator"
```

**Lưu ý:** System prompt KHÔNG chứa hardcoded schema — schema được lấy từ `state.schema_context` (dynamic).

---

### Agent 1.5: Table Selector & Column Pruner (`src/agents/table_selector.py` & `column_pruner.py`)

**Vai trò:** Giảm thiểu nhiễu thông tin (Noise Reduction) bằng cách chỉ giữ lại các bảng và cột thực sự cần thiết cho câu hỏi, tránh vượt quá Token Limit khi DB có quá nhiều bảng.

**Cơ chế hoạt động:**
1. **Table Selector:** 
   - Duyệt đồ thị (Graph Traversal) dựa trên Foreign Keys để tự động tìm các "bảng cầu nối" (Intermediate Tables). 
   - Tránh lỗi thiếu bảng trung gian khi JOIN.
2. **Column Pruner:**
   - Cắt tỉa các cột không liên quan, giúp LLM tập trung vào đúng dữ liệu.
   - Luôn luôn bảo toàn Primary Keys và Foreign Keys để đảm bảo SQL sinh ra không bị lỗi JOIN.

---

### Agent 2: Query Planner (`src/agents/query_planner.py`)

**Vai trò:** Lên kế hoạch chi tiết cho các query phức tạp.

**Chỉ chạy khi:** `plan_needed = True` (intent = join, complex, cte, subquery, aggregate)

**System prompt:** DYNAMIC — nhận `schema_context` từ state, KHÔNG hardcode schema.

**Output plan structure:**
```json
{
  "intent_summary": "Tổng doanh thu theo quốc gia",
  "query_type": "aggregate",
  "steps": [
    {"step_id": 1, "description": "Join Invoice với Customer", "tables_needed": ["Invoice", "Customer"]}
  ],
  "tables_used": ["Invoice", "Customer"],
  "columns_used": ["BillingCountry", "Total"],
  "estimated_complexity": "medium"
}
```

---

### Agent 3: SQL Generator (`src/agents/sql_generator.py`)

**Vai trò:** Sinh SQL query từ câu hỏi + schema context + plan.

**System Prompt — DYNAMIC:**

```python
SYSTEM_PROMPT_TEMPLATE = """You are an expert SQLite query generator.

DATABASE SCHEMA:
{schema}   ← injected from state.schema_context at runtime

CRITICAL RULES:
1. ONLY SELECT — never DROP/INSERT/UPDATE/DELETE
2. SQLite syntax: LIMIT 10 (not TOP 10), ROUND(col,2), strftime('%Y',date)
3. Define your own table aliases — do NOT use pre-defined aliases
4. Always use explicit column names from the schema above
5. Escape single quotes: '' not \\'  (e.g. WHERE Name = '90''s Music')
6. LIMIT must be integer: LIMIT 10 (not LIMIT 1st)
7. Always end SQL with semicolon

OUTPUT: strict JSON only — no markdown, no explanation outside JSON:
{{"sql":"SELECT ...;","confidence":0.9,"reasoning":"brief explanation"}}
"""
```

```python
def sql_generator_node(state):
    schema_text = _build_schema_text_for_prompt(state.schema_context)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text) + SQL_GENERATOR_RULES
    # → gọi Gemini với dynamic schema
```

**SQL Extraction (`_extract_sql()`):**
```
1. JSON: {"sql": "SELECT ..."}  → lấy trực tiếp
2. Markdown: ```sql SELECT ... ```  → regex extract
3. Plain SELECT: text.startswith("SELECT")  → dùng trực tiếp
4. Fallback: _build_fallback_sql() — generic keyword matching
```

---

### Agent 4: Validator (`src/agents/validator.py`) — DYNAMIC VALIDATION

**Vai trò:** Kiểm tra SQL bằng cách gọi DB thật (verify table/column tồn tại) + Python regex.

**4 bước kiểm tra (`hard_validate()`):**

```
1. SELECT/WITH check
   → Phải bắt đầu bằng SELECT hoặc WITH

2. Dangerous keywords check
   → DROP, DELETE, INSERT, UPDATE, TRUNCATE, ALTER, CREATE, GRANT, REVOKE, EXEC, EXECUTE

3. Hint patterns check (detect common LLM mistakes)
   → Track.Title → "Track.Name (not Title)"
   → Artist.GenreId → "Artist has no GenreId"
   → Invoice.AlbumId → "Invoice has no AlbumId"
   → Employee.CustomerId → "Employee has no CustomerId"

4. Dynamic table/column validation (requires DB connection)
   → _validate_tables(): db.table_exists() — case-insensitive
   → _validate_column_refs(): db.column_exists() — case-sensitive
     - Parse SQL → extract table.column references
     - For bare columns (no table prefix): check if ambiguous across tables
     - Resolve actual table names via LOWER(name)=LOWER(?) query
```

**Table/Column Name Resolution:**
```python
def _get_actual_table_name(db, table_lower: str) -> str:
    # SQLite is case-insensitive for table names
    # Resolve "album" → "Album" (actual case-preserved name)
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)=LOWER(?)",
        (table_lower,)
    )
    return row[0]  # actual case-preserved name

def _validate_column_refs(sql, db):
    # Build col_to_tables: {col_lower: [(actual_table, actual_col), ...]}
    # Case-sensitive: "name" matches column "Name" in actual DB
    for col_lower, matches in col_to_tables.items():
        if len(matches) == 0:
            issues.append(f"Column '{col}' not found in any table")
        elif len(matches) > 1:
            issues.append(f"Column '{col}' is ambiguous (exists in: {tables})")
```

**Retry khi fail:**
```python
if state.retry_count < state.max_retries:
    state.next_agent = "sql_generator"
    state.schema_context += f"\n\nSQL ERROR: {sql}\nISSUE: {issues_str}\nFix the SQL above."
else:
    state.error = f"Validation failed after {state.retry_count} retries"
    state.next_agent = "error"
```

---

### Agent 5: Executor (`src/agents/executor.py`)

**Vai trò:** Thực thi SQL trên SQLite, cache kết quả.

```python
# _fix_common_errors() chạy TRƯỚC KHI exec
sql_to_exec = _fix_common_errors(state.generated_sql)
result = db.execute_query(sql_to_exec)

# Success → cache
if result.error is None:
    cache.put(state.user_question, state.query_result, state.generated_sql)
    state.next_agent = "formatter"

# Execution error + còn retry → quay lại generator
```

**Fix pre-execution:**
```python
sql = re.sub(r'\bSELECT\s+TOP\s+(\d+)\b', 'SELECT', sql)           # TOP → bỏ
sql = re.sub(r'\bLIMIT\s+(\d+)(st|nd|rd|th)\b', r'LIMIT \1', sql)  # LIMIT 1st → LIMIT 1
sql = re.sub(r"\\'", "''", sql)                                      # \' → ''
```

---

### Agent 6: Result Formatter (`src/agents/result_formatter.py`)

**Vai trò:** Format kết quả SQL thành câu trả lời tự nhiên.

```json
{
  "summary": "Có 10 nghệ sĩ trong database",
  "detailed_answer": "1. Metallica — 10 albums\n2. Iron Maiden — 8 albums",
  "insights": ["Top 2 artists chiếm 18% tổng albums"],
  "visualization": {"recommended": true, "chart_type": "bar"},
  "sql": "SELECT ...;",
  "execution_time_ms": 45.2,
  "from_cache": false
}
```

---

## 4. Multi-Database System — Onboarding & Validation

### 4.1 Onboarding Flow

```
User provides DB path (upload / --db-path / selector)
        │
        ▼
DatabaseOnboarder.introspect(db_path)
  ├─ db.get_schema() → introspect all tables/columns/FKs
  ├─ db.get_table_columns() → per-table column names
  └─ db_hash = hash(db_path + schema_fingerprint)
        │
        ▼
Check schemas/{hash}.json cache
  ├─ HIT: load cached descriptions
  └─ MISS: generate_descriptions(db, schema)
        │
        ▼
LLM generates semantic descriptions per table
  (from table name, column names, types, FK relationships)
        │
        ▼
Optional: interactive refinement (CLI asks user 3-5 questions)
        │
        ▼
Save to schemas/{hash}.json
        │
        ▼
Rebuild schema index with semantic descriptions
  rebuild_schema_index(db, db_path=db_path)
        │
        ▼
Pipeline starts: orchestrator → ... → formatter
```

### 4.2 Schema Cache Storage (`schemas/`)

```
schemas/
├── {db_hash}.json         # Semantic descriptions (per-DB)
└── registry.json          # Index: db_path → metadata
```

**`{db_hash}.json` structure:**
```json
{
  "db_hash": "9f44f6f81652",
  "db_name": "Chinook",
  "onboarded_at": "2026-04-15T23:19:00",
  "tables": 11,
  "descriptions": {
    "Album": "Album nhạc — chứa thông tin album bao gồm title và artist liên quan.",
    "Artist": "Nghệ sĩ/ca sĩ — bảng danh sách tất cả nghệ sĩ trong hệ thống nhạc.",
    "Track": "Bài nhạc/track — thông tin chi tiết bài nhạc: name, album, genre..."
  }
}
```

**`registry.json` structure:**
```json
{
  "data/chinook/Chinook_Sqlite.sqlite": {
    "db_hash": "9f44f6f81652",
    "db_name": "Chinook",
    "table_count": 11,
    "onboarded_at": "2026-04-15T23:19:00"
  }
}
```

### 4.3 Dynamic Validation vs Hard-coded

| Aspek | Trước (Hard-coded) | Sau (Dynamic) |
|-------|-------------------|---------------|
| Table list | 11 bảng Chinook cứng | `db.table_exists()` → verify thật |
| Column list | 11 bảng hardcoded regex | `db.get_table_columns()` → verify thật |
| Semantic desc | `CHINOOK_DESCRIPTIONS` cứng | `schemas/{hash}.json` → từ onboarding |
| Schema trong prompt | Inline hardcoded CREATE TABLE | `state.schema_context` → dynamic |
| DB switch | Không hỗ trợ | `_ensure_db()` → rebuild tự động |

### 4.4 Dynamic Schema Text (no hardcoded tables)

```python
# Trước: prompts.py chứa toàn bộ CREATE TABLE statements
CHINOOK_SCHEMA = """\
CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name VARCHAR);
CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title VARCHAR, ArtistId ...);
...
"""

# Sau: schema được introspect từ DB thật tại runtime
def _build_direct_schema_context(db: DatabaseManager) -> str:
    schema = db.get_schema()
    parts = []
    for table in schema.tables:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in table.columns)
        parts.append(f"Table: {table.table_name}\nColumns: {cols}")
    return "\n\n".join(parts)
```

---

## 5. RAG & Embedding (Few-Shot Prompting & Cross-Lingual)

### Sự Đột Phá: Cross-Lingual Zero-Shot / Few-Shot
Hệ thống chứng minh khả năng **Cross-Lingual xuất sắc**. Thay vì Việt hoá cấu trúc Database (dẫn đến tối nghĩa, ví dụ cột `Ten` thay vì `Artist.Name`), hệ thống hoạt động chính xác nhất trên **Native English Schema**. LLM (Gemini) tự động ánh xạ từ câu hỏi tiếng Việt sang schema tiếng Anh với độ chính xác >90% ngay cả ở chế độ Zero-Shot.

### Unified Multi-Tenant Vector DB (FAISS)
Thay vì tạo ra hàng chục Vector DB riêng lẻ cho từng bộ Data, hệ thống sử dụng **FAISS Unified Database** (`faiss_unified_fewshot_db`).
- Tất cả câu SQL mẫu (Few-shot examples) của nhiều Database khác nhau được lưu chung.
- Quản lý cách ly thông qua **Metadata Filtering** (`dataset_type`).
- Ví dụ: Khi truy vấn `chinook_en`, FAISS chỉ trả về các câu mẫu được đánh tag `dataset="chinook_en"`, đảm bảo tính chính xác và dễ mở rộng cho doanh nghiệp.

### 5.1 Schema Indexer (`src/rag/schema_indexer.py`)

### 5.1 Schema Indexer (`src/rag/schema_indexer.py`)

Build documents từ DB schema tại startup:

```
Document types:
- table: full table description (name, columns, row_count, semantic desc from onboarding)
- column: per-column type + nullable + PK info
- relationship: foreign key relationships
```

**Semantic descriptions — từ onboarding (dynamic):**
```python
def _get_semantic_descriptions(db_path: str = "") -> dict[str, str]:
    schema, descriptions = get_current_db_schema(db_path or "", force_refresh=False)
    return descriptions or {}
```

### 5.2 Embedder (`src/rag/embedder.py`)

**Primary:** Gemini `models/gemini-embedding-001`
**Fallback 1:** TF-IDF vectorizer (384 dim) — khi không có API key
**Fallback 2:** SHA256 hash vectors (384 dim) — khi sklearn không có

### 5.3 Schema Retrieval (`get_schema_context_for_query()`)

```
1. FAISS Vector DB semantic search → top_k results
2. Fallback 1: keyword matching với in-memory _schema_docs_cache
   (keywords auto-generated từ actual table names — không cứng)
3. Fallback 2: build trực tiếp từ DB
```

---

## 6. Semantic Cache

**Vị trí:** Chỉ bật ở **executor level** — KHÔNG bật ở orchestrator.

**Lý do disable ở orchestrator:** Embedding similarity threshold (0.92) quá lỏng — "số lượng album" (COUNT) match "danh sách album" (SELECT Title) → trả về SQL hoàn toàn sai.

**Cache behavior:**
```
Executor success → cache.put(question, result, sql)
  → Embed question → cosine similarity check (threshold 0.92)
  → LRU eviction khi cache đầy (max 500)

Cache miss → continue pipeline normally
Cache hit → skip generator/executor → formatter
```

**LRU Eviction:** `OrderedDict` — `popitem(last=False)` xóa entry cũ nhất.

---

## 7. Evaluation & Checkpointing

### 7.1 Evaluation Script (`evaluate.py`)

**Checkpoint system:**
```
Checkpoint: data/eval_checkpoints/{dataset}_{category}_{fingerprint}.json
→ Tự động resume nếu crash giữa chừng
→ --no-resume: xóa checkpoint, chạy lại từ đầu
→ --clear-checkpoint: xóa checkpoint trước khi chạy
→ --clear-cache: xóa semantic cache trước khi chạy
```

**Metrics:**
```
PASS: sql_correct AND execution_success
  → sql_correct: ≥60% keywords matched
  → execution_success: no SQL error

Accuracy by Intent: aggregate / join / simple / complex
Accuracy by Tables Used: Album+Artist, Album+Artist+Track, etc.
Latency Distribution: < 5s / 5-10s / 10-20s / 20-30s / > 30s
```

---

## 8. Security & Safety

### 8.1 SQL Injection Prevention (3 layers)

| Layer | File | Check |
|-------|------|-------|
| Layer 1 | `database.py` | `sql.upper().startswith("SELECT")` |
| Layer 2 | `sql_generator.py` | `_validate_dangerous()` regex DROP/DELETE/INSERT/UPDATE |
| Layer 3 | `validator.py` | hard_validate() — SELECT/WITH + dangerous keywords |

### 8.2 Dangerous Keywords Blocked

```python
DANGEROUS_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE",
    "ALTER", "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE"
]
```

### 8.3 Case Sensitivity Handling

```
SQLite table names: case-insensitive
  → db.table_exists("album") → True (resolves to "Album")
  → Uses: LOWER(name)=LOWER(?) in SQL

SQLite column names: case-sensitive
  → db.column_exists("Artist", "Name") → True (checks actual "Name")
  → col_to_tables: builds from actual DB column names
```

---

## 9. Cấu Hình

### 9.1 File `.env`

```bash
# ── Google Gemini API (bắt buộc) ─────────────────────────────────────
GEMINI_API_KEY=AIza...

# ── LLM Models ─────────────────────────────────────────────────────────
LLM_MODEL=models/gemini-2.5-flash-lite
LLM_MODEL_FALLBACK=models/gemini-2.0-flash-exp

# ── Embedding Model ──────────────────────────────────────────────────
EMBEDDING_MODEL=models/gemini-embedding-001

# ── Database (default) ─────────────────────────────────────────────────
DB_PATH=data/chinook/Chinook_Sqlite.sqlite

# ── FAISS Vector DB (optional — có thể disabled trên Windows) ──────────────
CHROMA_PERSIST_DIR=./chroma_db

# ── Cache ──────────────────────────────────────────────────────────────
CACHE_SIMILARITY_THRESHOLD=0.92
CACHE_MAX_SIZE=500
```

---

## 10. Cấu Trúc File

```
text_to_sql/
│
├── evaluate.py                     # Script đánh giá hệ thống (benchmark)
├── generate_questions.py           # Script sinh câu hỏi bằng Gemini
├── main.py                         # CLI entry point (--db-path, --onboard, --list-dbs)
├── app.py                          # Streamlit Web UI (file upload, DB selector)
├── requirements.txt
├── .env
│
├── src/
│   ├── config.py                   # Load .env, Config class
│   ├── schema.py                   # Pydantic models: TableInfo, QueryResult, etc.
│   │
│   ├── graph.py                    # LangGraph workflow (6 nodes)
│   │   ├── _ensure_db()           # DB switch → rebuild schema index
│   │   ├── run_query()            # nhận db_path parameter
│   │   └── arun_query()           # async entry point
│   │
│   ├── agents/
│   │   ├── state.py               # AgentState — có current_db_path, current_db_schema
│   │   ├── gemini_llm.py          # Gemini client (invoke, retry, fallback)
│   │   ├── orchestrator.py        # Intent analysis + routing (dynamic schema)
│   │   ├── query_planner.py       # Execution plan (dynamic schema, no groq_llm)
│   │   ├── sql_generator.py       # SQL generation (dynamic SYSTEM_PROMPT)
│   │   ├── validator.py           # DYNAMIC validation — gọi DB thật
│   │   ├── executor.py           # SQL execution + cache.put()
│   │   ├── result_formatter.py   # Format kết quả (no groq_llm)
│   │   └── onboard.py            # DatabaseOnboarder — introspect + generate + cache
│   │
│   ├── db/
│   │   ├── database.py            # SQLite manager
│   │   │   ├── table_exists()     # case-insensitive via LOWER(name)=LOWER(?)
│   │   │   ├── get_table_columns() # case-preserved column names
│   │   │   └── execute_query()   # SELECT-only security check
│   │   └── data_pipeline.py
│   │
│   ├── rag/
│   │   ├── embedder.py           # Gemini embed + TF-IDF fallback
│   │   ├── chroma_store.py       # FAISS Vector DB operations
│   │   └── schema_indexer.py    # Schema → documents (dynamic, no CHINOOK_DESCRIPTIONS)
│   │
│   ├── memory/
│   │   └── semantic_cache.py    # In-memory LRU cache, cosine similarity
│   │
│   └── utils/
│       ├── logger.py             # UTF-8 safe logging (Windows cp1252 fix)
│       └── cli/
│           └── db_utils.py       # onboard_cmd(), list_cmd(), switch_cmd()
│
├── data/
│   ├── data.json                 # 300 câu hỏi (keyword-based eval)
│   ├── data_new_75.json         # 75 câu mới (gold_sql-based eval)
│   ├── chinook/
│   │   └── Chinook_Sqlite.sqlite # Default DB (11 bảng, ~15,600 rows)
│   └── eval_checkpoints/        # Checkpoint files
│
├── schemas/                      # Onboarding cache (per-DB semantic descriptions)
│   ├── {hash}.json              # Semantic descriptions per database
│   └── registry.json            # db_path → metadata index
│
├── chroma_db/                   # FAISS Vector DB persistent (optional)
└── docs/
    └── ARCHITECTURE.md          # (file này)
```

---

## 11. Hướng Dẫn Chạy

### 11.1 Khởi tạo

```bash
# Cài đặt dependencies
pip install -r requirements.txt

# Cấu hình .env
cp .env.example .env
# Thêm GEMINI_API_KEY

# Test nhanh
python main.py -q "Có bao nhiêu album?"
```

### 11.2 Multi-Database — CLI

```bash
# Query với database khác
python main.py -q "Top 5 artist có nhiều album nhất" --db-path mydata.sqlite

# Onboard một database mới
python main.py --onboard mydata.sqlite

# Liệt kê tất cả database đã onboard
python main.py --list-dbs

# Interactive mode với database cụ thể
python main.py --db-path mydata.sqlite
```

### 11.3 Multi-Database — Web UI

```bash
streamlit run app.py
# → Mở http://localhost:8501
```

Trong giao diện web:
- **Sidebar**: Chọn database từ danh sách đã onboard
- **Upload**: Upload file `.sqlite` / `.db` mới
- **Onboard & Use**: Tự động onboard + bắt đầu query

### 11.4 Evaluation

```bash
# Chạy 50 câu test (nhanh)
python evaluate.py --limit 50 --no-resume

# Chạy 300 câu đầy đủ
python evaluate.py --data data/data.json --output test/results.json

# Resume checkpoint
python evaluate.py

# Clear + chạy lại
python evaluate.py --no-resume --clear-cache
```

---

## Bảng So Sánh: Trước vs Sau Multi-DB Upgrade

| Thành phần | Trước | Sau |
|-----------|-------|-----|
| Schema trong prompts | `prompts.py` — hardcoded 11 bảng Chinook | Dynamic — introspect từ DB |
| Semantic descriptions | `CHINOOK_DESCRIPTIONS` hardcoded | `schemas/{hash}.json` từ onboarding |
| Validation | Regex cứng trên 11 bảng | `db.table_exists()` + `db.column_exists()` |
| SQL Generator prompts | Inline CREATE TABLE statements | `{schema}` template — dynamic injection |
| Orchestrator prompts | `prompts.py` ORCHESTRATOR_SYSTEM | Inline string — dynamic |
| Query Planner prompts | `prompts.py` QUERY_PLANNER_SYSTEM | Inline string — dynamic |
| Result Formatter prompts | `prompts.py` RESULT_FORMATTER_SYSTEM | Inline string — dynamic |
| DB switch | Không hỗ trợ | `_ensure_db()` → auto-rebuild |
| Upload DB mới | Không hỗ trợ | Streamlit file_uploader + onboard |
| LLM imports | `groq_llm` (BUG — wrong model) | `gemini_llm` (all agents) |
| Schema index rebuild | Chỉ 1 lần startup | Mỗi khi đổi DB |
