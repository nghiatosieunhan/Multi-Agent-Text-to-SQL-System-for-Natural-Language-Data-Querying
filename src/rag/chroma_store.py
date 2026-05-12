"""
ChromaDB Vector Store cho schema metadata.
NOTE: ChromaDB 1.x có segfault issues trên một số Windows environments.
Nếu ChromaDB không hoạt động, hệ thống tự động fallback sang direct schema lookup.
"""
import os
import structlog
from typing import Optional

from src.config import config

from src.schema import TableInfo, SchemaContext

log = structlog.get_logger("chroma_store")

# ChromaDB bị DISABLED trên Windows do segfault issues
# Fallback: dùng direct schema lookup thay vì vector search
_CHROMADB_DISABLED = os.name == "nt"  # Windows = always disable

_chroma_client = None
_collection = None


def _get_chroma_client():
    global _chroma_client
    if _CHROMADB_DISABLED:
        return None
    if _chroma_client is not None:
        return _chroma_client

    try:
        import chromadb
        from chromadb.config import Settings

        _chroma_client = chromadb.PersistentClient(
            path=config.CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        return _chroma_client
    except ImportError:
        log.warning("ChromaDB not installed")
        return None
    except Exception as e:
        log.warning("ChromaDB client creation failed", error=str(e))
        return None


def get_schema_collection():
    global _collection
    if _CHROMADB_DISABLED:
        return None
    if _collection is not None:
        return _collection

    try:
        client = _get_chroma_client()
        if client is None:
            return None
        _collection = client.get_or_create_collection(
            name="schema_metadata",
            metadata={"description": "Chinook schema metadata for Text-to-SQL RAG"},
        )
        return _collection
    except Exception as e:
        log.warning("ChromaDB collection failed", error=str(e))
        return None


def index_schema(schema_texts: list[dict], ids: Optional[list[str]] = None):
    """Index schema metadata — skip nếu ChromaDB disabled."""
    if _CHROMADB_DISABLED:
        log.info("ChromaDB disabled on Windows — using direct schema lookup")
        return

    try:
        collection = get_schema_collection()
        if collection is None:
            return

        ids_list = [s.get("id", f"schema_{i}") for i, s in enumerate(schema_texts)]
        docs = [s["text"] for s in schema_texts]
        metadata = [s.get("metadata", {}) for s in schema_texts]

        from src.rag.embedder import embed_with_retry
        embeddings = embed_with_retry(docs)

        collection.add(
            embeddings=embeddings,
            documents=docs,
            metadatas=metadata,
            ids=ids_list,
        )
        log.info("schema_indexed", count=len(docs))
    except Exception as e:
        log.warning("ChromaDB index failed", error=str(e))


def retrieve_schema_context(query: str, top_k: int = 5) -> list[dict]:
    """
    Retrieve top-k schema entries liên quan đến query.
    Nếu ChromaDB disabled: trả về direct schema lookup.
    """
    if _CHROMADB_DISABLED:
        # Fallback: trả về mô tả trực tiếp từ database
        return _fallback_schema_lookup(query, top_k)

    try:
        collection = get_schema_collection()
        if collection is None:
            return _fallback_schema_lookup(query, top_k)

        from src.rag.embedder import embed_with_retry
        query_emb = embed_with_retry([query])[0]

        count = collection.count()
        if count == 0:
            return _fallback_schema_lookup(query, top_k)

        results = collection.query(
            query_embeddings=[query_emb],
            n_results=min(top_k, count),
            include=["documents", "metadatas", "distances"],
        )

        retrieved = []
        ids_list = results.get("ids", [[]])[0]
        docs_list = results.get("documents", [[]])[0]
        metas_list = results.get("metadatas", [[]])[0]
        dists_list = results.get("distances", [[]])[0]

        for i in range(len(ids_list)):
            retrieved.append({
                "id": ids_list[i],
                "document": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
                "distance": dists_list[i] if i < len(dists_list) else None,
            })

        return retrieved

    except Exception as e:
        log.warning("ChromaDB retrieve failed", error=str(e))
        return _fallback_schema_lookup(query, top_k)


def _fallback_schema_lookup(query: str, top_k: int = 5) -> list[dict]:
    """
    Fallback: trả về schema context trực tiếp từ database thay vì vector search.
    Dùng keyword matching đơn giản để chọn relevant tables.
    """
    try:
        from src.db import get_db_manager
        db = get_db_manager()
        schema = db.get_schema()

        query_lower = query.lower()
        scored_tables = []

        for table in schema.tables:
            score = 0
            # Score dựa trên keyword match
            keywords_map = {
                "album": ["album", "nhạc", "music"],
                "artist": ["artist", "ca sĩ", "nghệ sĩ", "singer", "band"],
                "customer": ["customer", "khách", "khách hàng", "buyer"],
                "employee": ["employee", "nhân viên", "staff", "agent"],
                "genre": ["genre", "thể loại", "loại nhạc", "type music"],
                "invoice": ["invoice", "hóa đơn", "billing", "đơn hàng"],
                "invoiceline": ["invoiceline", "chi tiết", "line item"],
                "mediatype": ["media", "format", "định dạng"],
                "playlist": ["playlist", "danh sách phát"],
                "playlisttrack": ["playlisttrack"],
                "track": ["track", "bài hát", "song", "nhạc", "title"],
            }

            table_keywords = keywords_map.get(table.table_name.lower(), [])
            for kw in table_keywords:
                if kw in query_lower:
                    score += 1

            # Check column names
            for col in table.columns:
                col_name_lower = col["name"].lower()
                for word in query_lower.split():
                    if word in col_name_lower and len(word) > 2:
                        score += 0.5

            scored_tables.append((score, table))

        # Sort by score, lấy top_k
        scored_tables.sort(key=lambda x: x[0], reverse=True)
        top_tables = scored_tables[:top_k]

        results = []
        for score, table in top_tables:
            if score > 0:
                col_lines = []
                for c in table.columns:
                    pk = " [PRIMARY KEY]" if c.get("pk") else ""
                    col_lines.append(f"- {c['name']} ({c['type']}, "
                                     f"{'NULL' if c.get('nullable') else 'NOT NULL'}{pk})")

                doc = f"Table: {table.table_name}\n" \
                      f"Rows: ~{table.row_count}\nColumns:\n" + "\n".join(col_lines)
                results.append({
                    "id": f"fallback_{table.table_name}",
                    "document": doc,
                    "metadata": {"type": "table", "table_name": table.table_name},
                    "distance": 0.0,
                })

        # Nếu không có match, trả về tất cả tables
        if not results:
            for table in schema.tables[:top_k]:
                col_lines = []
                for c in table.columns:
                    col_lines.append(f"- {c['name']} ({c['type']})")
                doc = f"Table: {table.table_name}\nColumns:\n" + "\n".join(col_lines)
                results.append({
                    "id": f"fallback_{table.table_name}",
                    "document": doc,
                    "metadata": {"type": "table", "table_name": table.table_name},
                    "distance": 1.0,
                })

        return results

    except Exception as e:
        log.warning("fallback_schema_lookup failed", error=str(e))
        return []


def clear_schema_index():
    """Xóa schema index."""
    if _CHROMADB_DISABLED:
        return

    try:
        collection = get_schema_collection()
        if collection is None:
            return
        try:
            collection.delete(where={})
        except TypeError:
            collection.delete(where="")
        except Exception:
            pass
        log.info("schema_index_cleared")
    except Exception as e:
        log.warning("ChromaDB clear failed", error=str(e))
