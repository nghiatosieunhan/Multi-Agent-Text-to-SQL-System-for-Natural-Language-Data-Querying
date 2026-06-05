"""
Semantic Cache — lưu trữ và reuse kết quả query dựa trên similarity.
Dùng embedding cosine similarity để detect semantically equivalent queries.
"""
import time
import hashlib
import json
from collections import OrderedDict
from typing import Optional

import structlog
from src.config import config
from src.rag.embedder import embed_single
from src.schema import CachedResult

log = structlog.get_logger("sem_cache")


class SemanticCache:
    """
    Lightweight semantic cache với cosine similarity.
    - LRU eviction khi cache đầy
    - Similarity threshold: config.CACHE_SIMILARITY_THRESHOLD
    """

    def __init__(self, max_size: Optional[int] = None, threshold: Optional[float] = None):
        self.max_size = max_size or config.CACHE_MAX_SIZE
        self.threshold = threshold or config.CACHE_SIMILARITY_THRESHOLD
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ── Cosine similarity ────────────────────────────────────────────────
    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Tính cosine similarity giữa 2 vectors."""
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ── Cache key ─────────────────────────────────────────────────────────
    def _make_key(self, text: str) -> str:
        """Tạo deterministic cache key từ câu hỏi."""
        return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]

    # ── Public API ────────────────────────────────────────────────────────
    def get(self, question: str) -> Optional[tuple[dict, str]]:
        """
        Kiểm tra xem câu hỏi đã có trong cache chưa.
        Trả về (cached result, sql) nếu similarity >= threshold, None nếu miss.
        """
        question_emb = embed_single(question)
        if not question_emb:
            return None

        for key, entry in reversed(list(self._cache.items())):
            cached_emb = entry.get("question_embedding", [])
            if not cached_emb:
                continue
            similarity = self._cosine_similarity(question_emb, cached_emb)
            if similarity >= self.threshold:
                # Anti-hallucination: Intent Matcher (Jaccard Similarity)
                # Dù Cosine Similarity cao (Vector giống nhau), chúng ta vẫn phải kiểm tra
                # xem các "Từ khóa thực thể" (Noun/Entities) có khớp nhau không.
                # Anti-hallucination: Intent Matcher (Jaccard Similarity) với Underthesea
                # Dùng thư viện tách từ Tiếng Việt để gom các từ ghép (VD: "bài hát" thay vì "bài", "hát")
                import re
                try:
                    from underthesea import word_tokenize
                except ImportError:
                    # Fallback nếu chưa cài underthesea
                    def word_tokenize(text, format):
                        return re.findall(r'\b\w+\b', text)
                        
                def get_keywords(text: str) -> set:
                    # Tách từ tiếng Việt, các từ ghép sẽ được nối bằng dấu '_'
                    words = word_tokenize(text.lower(), format="text").split()
                    stop_words = {"của", "trong", "có", "là", "những", "các", "cho", "biết", "bao", "nhiêu", "nào", "được", "với", "như", "thế"}
                    return set(w.replace("_", " ") for w in words if w not in stop_words and len(w) > 1)
                
                kw1 = get_keywords(question)
                kw2 = get_keywords(entry.get("question", ""))
                
                intersection = len(kw1.intersection(kw2))
                union = len(kw1.union(kw2))
                jaccard = intersection / union if union > 0 else 0
                
                if jaccard < self.threshold:
                    log.info("cache_rejected_by_intent", original=question, cached=entry.get("question", ""), jaccard=round(jaccard, 2))
                    continue  # Intent conflict, reject cache hit
                
                # Move to end (LRU update)
                self._cache.move_to_end(key)
                self._hits += 1
                log.info(
                    "cache_hit",
                    key=key,
                    similarity=round(similarity, 4),
                    original_question=entry.get("question", "")[:50],
                )
                result = entry["result"]
                result["from_cache"] = True
                return result, entry["sql"]

        self._misses += 1
        return None

    def put(self, question: str, result: dict, sql: str):
        """Lưu kết quả query vào cache."""
        question_emb = embed_single(question)
        key = self._make_key(question)

        if len(self._cache) >= self.max_size:
            # Evict least recently used
            evicted_key, _ = self._cache.popitem(last=False)
            log.info("cache_evicted", evicted_key=evicted_key)

        self._cache[key] = {
            "question": question,
            "question_embedding": question_emb,
            "result": result,
            "sql": sql,
            "created_at": time.time(),
        }
        log.info("cache_put", key=key, sql_snippet=sql[:60])

    def invalidate(self, table_name: Optional[str] = None):
        """Xóa cache entries liên quan đến bảng (khi data thay đổi)."""
        if table_name is None:
            self._cache.clear()
            log.info("cache_cleared")
            return

        to_remove = [
            k for k, v in self._cache.items()
            if table_name in v.get("sql", "").lower()
        ]
        for k in to_remove:
            del self._cache[k]
        log.info("cache_invalidated", table=table_name, removed=len(to_remove))

    def stats(self) -> dict:
        """Trả về cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
        }


# Global singleton
_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
