"""
Schema Indexer — build và quản lý schema knowledge base cho RAG.
Chạy offline một lần khi khởi động.

DYNAMIC: Semantic descriptions được lấy từ hệ thống onboarding
(schemas/{hash}.json) thay vì hardcode Chinook.
"""
from typing import Optional

import structlog
from src.db import DatabaseManager
from src.rag.chroma_store import index_schema, clear_schema_index, retrieve_schema_context
from src.schema import TableInfo

log = structlog.get_logger("schema_indexer")

# Cache schema documents in memory (dùng khi ChromaDB disabled)
_schema_docs_cache: list[dict] = []


def _get_semantic_descriptions(db_path: str = "") -> dict[str, str]:
    """
    Lấy semantic descriptions từ hệ thống onboarding.
    Fallback về empty dict nếu chưa onboard.
    """
    try:
        from src.agents.onboard import get_current_db_schema
        schema, descriptions = get_current_db_schema(db_path or "", force_refresh=False)
        return descriptions or {}
    except Exception:
        return {}


def _build_schema_text(schema, db: DatabaseManager) -> str:
    """Build raw schema text từ DB introspection (no semantic desc)."""
    parts = []
    for table in schema.tables:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in table.columns)
        parts.append(f"Table: {table.table_name}\nColumns: {cols}")
    return "\n\n".join(parts)


def build_schema_documents(db: DatabaseManager, db_path: str = "") -> list[dict]:
    """
    Chuyển schema DB thành documents cho RAG indexing.
    Dùng semantic descriptions từ onboarding (dynamic) hoặc build không có mô tả.
    """
    schema = db.get_schema()

    # Lấy semantic descriptions từ hệ thống onboarding
    semantic = _get_semantic_descriptions(db_path or "")

    docs = []

    for table in schema.tables:
        semantic_desc = semantic.get(table.table_name, "")
        table_doc = _build_table_doc(table, semantic_desc)
        docs.append(table_doc)

        for col in table.columns:
            col_doc = _build_column_doc(table.table_name, col)
            docs.append(col_doc)

    for rel in schema.relationships:
        rel_doc = _build_relationship_doc(rel)
        docs.append(rel_doc)

    return docs


def _build_table_doc(table: TableInfo, semantic_desc: str = "") -> dict:
    col_descriptions = []
    for c in table.columns:
        pk_marker = " [PRIMARY KEY]" if c.get("pk") else ""
        nullable = "NULL" if c.get("nullable") else "NOT NULL"
        col_descriptions.append(
            f"- {c['name']} ({c['type']}, {nullable}{pk_marker})"
        )

    text = f"""Table: {table.table_name}
Description: {semantic_desc or 'No description available.'}
Row count: approximately {table.row_count} rows
Columns:
{chr(10).join(col_descriptions)}"""

    return {
        "id": f"table_{table.table_name}",
        "text": text,
        "metadata": {
            "type": "table",
            "table_name": table.table_name,
            "row_count": table.row_count,
        },
    }


def _build_column_doc(table_name: str, col: dict) -> dict:
    text = (
        f"Column: {table_name}.{col['name']} "
        f"Type: {col['type']} "
        f"{'Nullable' if col.get('nullable') else 'Not Nullable'} "
        f"{'[Primary Key]' if col.get('pk') else ''}"
    )
    return {
        "id": f"col_{table_name}_{col['name']}",
        "text": text,
        "metadata": {
            "type": "column",
            "table_name": table_name,
            "column_name": col["name"],
        },
    }


def _build_relationship_doc(rel: dict) -> dict:
    text = (
        f"Relationship: {rel['from_table']}.{rel['from_column']} "
        f"references {rel['to_table']}.{rel['to_column']}"
    )
    return {
        "id": f"rel_{rel['from_table']}_{rel['to_table']}",
        "text": text,
        "metadata": {
            "type": "relationship",
            "from_table": rel["from_table"],
            "to_table": rel["to_table"],
        },
    }


def rebuild_schema_index(db: Optional[DatabaseManager] = None, db_path: str = ""):
    """
    Rebuild toàn bộ schema index trong ChromaDB.
    Lưu cache in-memory để fallback dùng khi ChromaDB disabled.
    """
    global _schema_docs_cache

    if db is None:
        db = DatabaseManager()

    log.info("rebuilding_schema_index")

    # Build documents (dynamic, using onboarding semantic descriptions)
    docs = build_schema_documents(db, db_path=db_path)
    _schema_docs_cache = docs  # Lưu vào memory cho fallback

    # Index vào ChromaDB (sẽ tự skip nếu disabled)
    try:
        clear_schema_index()
        index_schema(docs)
        log.info("schema_index_rebuilt", documents=len(docs))
    except Exception as e:
        log.warning("schema_index_rebuild_skipped", error=str(e))


def get_schema_context_for_query(
    query: str,
    db: Optional[DatabaseManager] = None,
    top_k: int = 5,
    pruning_mode: str = "auto",
) -> str:
    """
    Lấy schema context. Phiên bản mới: Sử dụng Two-Stage Table Selection (Ý tưởng A).
    - Bước 1: Lấy danh sách toàn bộ bảng.
    - Bước 2: Dùng LLM (Table Selector Agent) để lọc ra chính xác các bảng cần dùng.
    - Bước 3: Chỉ trả về cấu trúc chi tiết của các bảng đã lọc.
    """
    if db is None:
        try:
            from src.db import get_db_manager
            db = get_db_manager()
        except Exception:
            return ""

    schema = db.get_schema()
    semantic = _get_semantic_descriptions(db.db_path)
    
    if pruning_mode not in {"auto", "force", "bypass"}:
        raise ValueError(f"Unsupported schema pruning mode: {pruning_mode}")

    # Auto mode uses the production threshold; force/bypass exist for paired evaluation.
    should_bypass = pruning_mode == "bypass" or (
        pruning_mode == "auto" and len(schema.tables) < 30
    )
    if should_bypass:
        selected_table_names = [t.table_name for t in schema.tables]
        pruned_lower_map = {}
    else:
        # --- BƯỚC ĐỘT PHÁ 1: TABLE SELECTOR AGENT (Ý tưởng A) ---
        from src.agents.table_selector import select_tables_for_query
        selected_table_names = select_tables_for_query(query, db, semantic)
        
        # --- BƯỚC ĐỘT PHÁ 2: COLUMN PRUNER AGENT (Ý tưởng B) ---
        from src.agents.column_pruner import prune_columns_for_query
        pruned_columns_map = prune_columns_for_query(query, db, selected_table_names)
        # Chuẩn hóa pruned_columns_map về lowercase để tránh lỗi case-sensitive từ LLM
        pruned_lower_map = {}
        if pruned_columns_map:
            for t, cols in pruned_columns_map.items():
                pruned_lower_map[t.lower()] = set(c.lower() for c in cols)

    parts = []
    for table in schema.tables:
        if table.table_name not in selected_table_names:
            continue # BỎ QUA CÁC BẢNG KHÔNG CẦN THIẾT!
            
        semantic_desc = semantic.get(table.table_name, "")
        
        # Cắt tỉa cột theo kết quả của Column Pruner (nếu có)
        filtered_cols = table.columns
        table_lower = table.table_name.lower()
        if table_lower in pruned_lower_map:
            allowed_cols = pruned_lower_map[table_lower]
            filtered_cols = [c for c in table.columns if c['name'].lower() in allowed_cols]
            # Nếu lỡ cắt hết thì lấy lại toàn bộ để an toàn
            if not filtered_cols:
                filtered_cols = table.columns
                
        cols = ", ".join(f"{c['name']} ({c['type']})" for c in filtered_cols)
        
        # Thêm thông tin Foreign Keys để LLM biết cách JOIN
        fks = [rel for rel in schema.relationships if rel["from_table"] == table.table_name]
        fk_str = ""
        if fks:
            fk_str = "\nForeign Keys: " + ", ".join(f"{rel['from_column']} -> {rel['to_table']}.{rel['to_column']}" for rel in fks)
            
        parts.append(f"Table: {table.table_name}\nDescription: {semantic_desc}\nColumns: {cols}{fk_str}")
        
    return "\n\n".join(parts)


def _keyword_match_schema(query: str, docs: list[dict], top_k: int) -> str:
    """
    Keyword match đơn giản khi không có vector search.
    DYNAMIC: build keyword map từ actual table names trong docs.
    """
    query_lower = query.lower()

    # Build keyword map dynamically from table names
    table_keywords: dict[str, list[str]] = {}
    for doc in docs:
        if doc["metadata"].get("type") != "table":
            continue
        tbl = doc["metadata"]["table_name"]
        tbl_lower = tbl.lower()
        # Auto-generate keywords from table name
        keywords = [tbl_lower]
        # Split CamelCase: AlbumTrack → album + track
        import re
        parts = re.findall(r'[A-Z][a-z]+', tbl)
        for p in parts:
            keywords.append(p.lower())
        # Add full table name as-is
        keywords.append(tbl)
        table_keywords[tbl_lower] = keywords

    scored = []
    for doc in docs:
        if doc["metadata"].get("type") != "table":
            continue
        tbl = doc["metadata"]["table_name"].lower()
        score = 0
        kws = table_keywords.get(tbl, [])
        for kw in kws:
            if kw in query_lower:
                score += 1
        scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [d for _, d in scored[:top_k] if _ > 0]
    if not top:
        top = [d for _, d in scored[:3]]

    return "\n---\n".join(d["text"] for d in top)


def _build_direct_schema_context(db: DatabaseManager) -> str:
    """Build schema context trực tiếp từ DB (emergency fallback)."""
    schema = db.get_schema()
    parts = []
    for table in schema.tables:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in table.columns)
        parts.append(f"Table: {table.table_name}\nColumns: {cols}")
    return "\n\n".join(parts)


def parse_spider_schema_input(spider_input: str) -> str:
    """
    Parse Spider dataset 'input' field → schema context string.

    Input format:
    [INST] Here is a database schema:
    stadium :
    Stadium_ID [ INT ] primary_key
    Location [ TEXT ]
    ...

    Output: schema context string cho agents
    """
    if not spider_input:
        return "No schema available."

    # Strip the instruction prefix
    lines = spider_input.strip().split("\n")
    schema_lines = []
    in_schema = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("[INST]"):
            if "database schema" in stripped.lower():
                in_schema = True
            continue
        if in_schema:
            schema_lines.append(stripped)

    # Parse table blocks: "table_name :" followed by column lines
    parts = []
    current_table = None
    current_cols = []

    for line in schema_lines:
        # Table header: "table_name :" or "table_name : alias"
        if line.endswith(" :") or (": " in line and not line.startswith("-") and "[" not in line):
            # Save previous table
            if current_table:
                cols_str = "\n".join(current_cols) if current_cols else "No columns"
                parts.append(f"Table: {current_table}\nColumns:\n{cols_str}")

            # Start new table
            colon_idx = line.find(":")
            current_table = line[:colon_idx].strip()
            current_cols = []
        elif current_table and "[" in line:
            # Column line: "column_name [ TYPE ] constraints"
            bracket_start = line.find("[")
            bracket_end = line.find("]")
            if bracket_start != -1 and bracket_end != -1:
                col_name = line[:bracket_start].strip()
                col_type_raw = line[bracket_start+1:bracket_end].strip()
                # Simplify type: "PRIMARY KEY INTEGER" -> "INTEGER"
                # or just extract the type
                col_parts = col_type_raw.split()
                col_type = col_parts[0] if col_parts else "TEXT"
                current_cols.append(f"- {col_name} ({col_type})")

    # Save last table
    if current_table:
        cols_str = "\n".join(current_cols) if current_cols else "No columns"
        parts.append(f"Table: {current_table}\nColumns:\n{cols_str}")

    return "\n\n".join(parts) if parts else "No schema available."


def build_schema_from_create_text(create_text: str) -> str:
    """
    Parse Spider's CREATE TABLE format → schema context string.
    Input: "CREATE TABLE head (age INTEGER);\nCREATE TABLE department (name VARCHAR)"
    Output: schema context string cho agents
    """
    if not create_text:
        return "No schema available."

    statements = create_text.strip().split(";")
    parts = []

    for stmt in statements:
        stmt = stmt.strip()
        if not stmt or not stmt.upper().startswith("CREATE TABLE"):
            continue

        # Extract table name
        rest = stmt[len("CREATE TABLE"):].strip()
        # Handle "TABLE_NAME (" or "TABLE_NAME AS ("
        paren_idx = rest.find("(")
        if paren_idx == -1:
            continue

        table_name = rest[:paren_idx].strip().strip("AS").strip()

        # Extract columns
        cols_str = rest[paren_idx + 1 : rest.rfind(")")]
        cols = []
        for col_def in cols_str.split(","):
            col_def = col_def.strip()
            if not col_def:
                continue
            # Split on first space
            parts_col = col_def.split(None, 1)
            if len(parts_col) >= 1:
                col_name = parts_col[0].strip()
                col_type = parts_col[1].strip() if len(parts_col) > 1 else "TEXT"
                cols.append(f"- {col_name} ({col_type})")

        parts.append(f"Table: {table_name}\nColumns:\n" + "\n".join(cols))

    return "\n\n".join(parts) if parts else "No schema available."
