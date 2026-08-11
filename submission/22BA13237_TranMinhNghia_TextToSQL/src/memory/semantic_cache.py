"""In-memory semantic cache với exact match, semantic match và diagnostics."""

import hashlib
import os
import re
import time
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
from threading import RLock
from typing import Optional

import structlog

from src.config import config
from src.rag.embedder import embed_single_with_metadata

try:
    from underthesea import word_tokenize

    def _tokenize(text: str) -> str:
        return word_tokenize(text.lower(), format="text")
except ImportError:

    def _tokenize(text: str) -> str:
        return " ".join(re.findall(r"\b\w+\b", text.lower(), flags=re.UNICODE))


log = structlog.get_logger("sem_cache")

_DEFAULT_NAMESPACE = "__default__"
_STOP_WORDS = {
    "của", "trong", "có", "là", "những", "các", "cho", "biết", "bao",
    "nhiêu", "nào", "được", "với", "như", "thế", "xem", "tìm", "hãy",
    "hiển", "thị", "liệt", "kê", "hiển_thị", "liệt_kê", "bảng",
    "dữ_liệu", "tôi", "cho_tôi", "vui", "lòng",
}


def normalize_question(text: str) -> str:
    """Chuẩn hóa ổn định nhưng vẫn giữ số và toán tử có ý nghĩa."""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = normalized.replace("!=", " __operator_ne__ ")
    normalized = re.sub(r"[^\w\s<>=.%+-]", " ", normalized, flags=re.UNICODE)
    normalized = normalized.replace("__operator_ne__", "!=")
    return " ".join(normalized.split())


def get_keywords(text: str) -> set[str]:
    words = _tokenize(normalize_question(text)).split()
    keywords = [
        word.replace("_", " ")
        for word in words
        if word not in _STOP_WORDS and len(word) > 1
    ]
    bigrams = [f"{left} {right}" for left, right in zip(keywords, keywords[1:])]
    return set(keywords + bigrams)


def get_critical_tokens(text: str) -> set[str]:
    """Trích xuất số, toán tử và hướng sắp xếp để tránh cache nhầm."""
    normalized = normalize_question(text)
    numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", normalized))
    operators = set(re.findall(r">=|<=|!=|>|<|=", normalized))
    directional = set(re.findall(r"\b(?:top|nhất|đầu|cuối)\b", normalized))
    return numbers | operators | directional


class SemanticCache:
    """LRU semantic cache được cô lập theo database namespace."""

    def __init__(
        self,
        max_size: Optional[int] = None,
        threshold: Optional[float] = None,
        jaccard_threshold: Optional[float] = None,
    ):
        self.max_size = max_size if max_size is not None else config.CACHE_MAX_SIZE
        self.threshold = (
            threshold if threshold is not None else config.CACHE_SIMILARITY_THRESHOLD
        )
        self.jaccard_threshold = (
            jaccard_threshold
            if jaccard_threshold is not None
            else getattr(config, "CACHE_JACCARD_THRESHOLD", 0.65)
        )
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._counters: defaultdict[str, Counter] = defaultdict(Counter)
        self._last_lookup_by_namespace: dict[str, dict] = {}
        self._last_lookup_global: dict = {}
        self._lock = RLock()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _namespace_id(namespace: Optional[str]) -> str:
        if not namespace:
            normalized = _DEFAULT_NAMESPACE
        else:
            raw = str(namespace).strip()
            if "://" in raw:
                normalized = raw
            else:
                normalized = os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _make_key(self, text: str, namespace: Optional[str] = None) -> str:
        namespace_id = self._namespace_id(namespace)
        payload = f"{namespace_id}:{normalize_question(text)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _embedding_compatible(left: dict, right: dict) -> bool:
        return bool(
            left
            and right
            and left.get("backend") == right.get("backend")
            and left.get("model") == right.get("model")
            and left.get("dimension") == right.get("dimension")
            and left.get("dimension", 0) > 0
        )

    def _finish_lookup(
        self,
        namespace_id: str,
        status: str,
        reason: str,
        started_at: float,
        cosine: Optional[float] = None,
        jaccard: Optional[float] = None,
    ) -> None:
        diagnostic = {
            "status": status,
            "reason": reason,
            "cosine": round(cosine, 4) if cosine is not None else None,
            "jaccard": round(jaccard, 4) if jaccard is not None else None,
            "cosine_threshold": self.threshold,
            "jaccard_threshold": self.jaccard_threshold,
            "lookup_time_ms": round((time.perf_counter() - started_at) * 1000, 2),
        }
        with self._lock:
            counters = self._counters[namespace_id]
            if status == "exact_hit":
                counters["exact_hits"] += 1
            elif status == "semantic_hit":
                counters["semantic_hits"] += 1
            else:
                counters["misses"] += 1
            self._last_lookup_by_namespace[namespace_id] = diagnostic
            self._last_lookup_global = diagnostic

    @staticmethod
    def _result_from_entry(entry: dict) -> tuple[dict, str]:
        result = deepcopy(entry["result"])
        result["from_cache"] = True
        return result, entry["sql"]

    def get(self, question: str, namespace: Optional[str] = None) -> Optional[tuple[dict, str]]:
        """Trả cache hit trong cùng namespace, ưu tiên exact match."""
        started_at = time.perf_counter()
        namespace_id = self._namespace_id(namespace)
        exact_key = self._make_key(question, namespace)

        with self._lock:
            exact_entry = self._cache.get(exact_key)
            if exact_entry and exact_entry.get("namespace_id") == namespace_id:
                self._cache.move_to_end(exact_key)
                result = self._result_from_entry(exact_entry)
            else:
                result = None

        if result is not None:
            self._finish_lookup(namespace_id, "exact_hit", "exact_key", started_at, 1.0, 1.0)
            log.info("cache_exact_hit", key=exact_key)
            return result

        question_embedding, question_metadata = embed_single_with_metadata(
            question,
            task_type="RETRIEVAL_QUERY",
        )
        if not question_embedding:
            self._finish_lookup(namespace_id, "miss", "embedding_unavailable", started_at)
            return None

        with self._lock:
            candidates = [
                (key, entry.copy())
                for key, entry in reversed(self._cache.items())
                if entry.get("namespace_id") == namespace_id
            ]

        if not candidates:
            self._finish_lookup(namespace_id, "miss", "no_entries", started_at)
            return None

        best_cosine = -1.0
        best_jaccard: Optional[float] = None
        best_reason = "cosine_below_threshold"

        for key, entry in candidates:
            cached_embedding = entry.get("question_embedding", [])
            cached_metadata = entry.get("embedding_metadata", {})

            if not self._embedding_compatible(question_metadata, cached_metadata):
                cached_embedding, cached_metadata = embed_single_with_metadata(
                    entry.get("question", ""),
                    task_type="RETRIEVAL_DOCUMENT",
                )
                if self._embedding_compatible(question_metadata, cached_metadata):
                    with self._lock:
                        if key in self._cache:
                            self._cache[key]["question_embedding"] = cached_embedding
                            self._cache[key]["embedding_metadata"] = cached_metadata
                else:
                    if best_cosine < 0:
                        best_reason = "embedding_backend_mismatch"
                    continue

            similarity = self._cosine_similarity(question_embedding, cached_embedding)
            if similarity > best_cosine:
                best_cosine = similarity
                best_jaccard = None
                best_reason = "cosine_below_threshold"
            if similarity < self.threshold:
                continue

            keywords_a = get_keywords(question)
            keywords_b = get_keywords(entry.get("question", ""))
            union = keywords_a | keywords_b
            jaccard = len(keywords_a & keywords_b) / len(union) if union else 1.0
            if similarity >= best_cosine:
                best_jaccard = jaccard

            if jaccard < self.jaccard_threshold:
                best_reason = "jaccard_below_threshold"
                continue

            if get_critical_tokens(question) != get_critical_tokens(entry.get("question", "")):
                best_reason = "critical_token_mismatch"
                continue

            with self._lock:
                live_entry = self._cache.get(key)
                if live_entry is None:
                    continue
                self._cache.move_to_end(key)
                result = self._result_from_entry(live_entry)

            self._finish_lookup(
                namespace_id,
                "semantic_hit",
                "semantic_match",
                started_at,
                similarity,
                jaccard,
            )
            log.info("cache_semantic_hit", key=key, similarity=round(similarity, 4))
            return result

        reported_cosine = best_cosine if best_cosine >= 0 else None
        self._finish_lookup(
            namespace_id,
            "miss",
            best_reason,
            started_at,
            reported_cosine,
            best_jaccard,
        )
        log.info("cache_miss", reason=best_reason, similarity=reported_cosine)
        return None

    def put(
        self,
        question: str,
        result: dict,
        sql: str,
        namespace: Optional[str] = None,
    ) -> None:
        """Lưu một kết quả vào namespace tương ứng."""
        question_embedding, embedding_metadata = embed_single_with_metadata(
            question,
            task_type="RETRIEVAL_DOCUMENT",
        )
        key = self._make_key(question, namespace)
        namespace_id = self._namespace_id(namespace)

        with self._lock:
            if key not in self._cache and len(self._cache) >= self.max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                log.info("cache_evicted", evicted_key=evicted_key)
            self._cache[key] = {
                "namespace_id": namespace_id,
                "question": question,
                "normalized_question": normalize_question(question),
                "question_embedding": question_embedding,
                "embedding_metadata": embedding_metadata,
                "result": deepcopy(result),
                "sql": sql,
                "created_at": time.time(),
            }
            self._cache.move_to_end(key)
        log.info("cache_put", key=key, sql_snippet=(sql or "")[:60])

    def invalidate(
        self,
        table_name: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> None:
        """Xóa cache toàn bộ, theo database, hoặc theo bảng trong database."""
        namespace_id = self._namespace_id(namespace) if namespace is not None else None
        with self._lock:
            if table_name is None:
                if namespace_id is None:
                    self._cache.clear()
                    self._counters.clear()
                    self._last_lookup_by_namespace.clear()
                    self._last_lookup_global = {}
                else:
                    for key in [
                        key
                        for key, entry in self._cache.items()
                        if entry.get("namespace_id") == namespace_id
                    ]:
                        del self._cache[key]
                    self._counters.pop(namespace_id, None)
                    self._last_lookup_by_namespace.pop(namespace_id, None)
                log.info("cache_cleared", namespace_scoped=namespace_id is not None)
                return

            table = table_name.casefold()
            keys = [
                key
                for key, entry in self._cache.items()
                if (namespace_id is None or entry.get("namespace_id") == namespace_id)
                and table in (entry.get("sql") or "").casefold()
            ]
            for key in keys:
                del self._cache[key]
        log.info("cache_invalidated", table=table_name, removed=len(keys))

    def stats(self, namespace: Optional[str] = None) -> dict:
        """Thống kê aggregate hoặc riêng cho một database namespace."""
        with self._lock:
            if namespace is None:
                counters = Counter()
                for namespace_counters in self._counters.values():
                    counters.update(namespace_counters)
                size = len(self._cache)
            else:
                namespace_id = self._namespace_id(namespace)
                counters = self._counters[namespace_id].copy()
                size = sum(
                    entry.get("namespace_id") == namespace_id
                    for entry in self._cache.values()
                )

        exact_hits = counters["exact_hits"]
        semantic_hits = counters["semantic_hits"]
        hits = exact_hits + semantic_hits
        misses = counters["misses"]
        total = hits + misses
        return {
            "size": size,
            "max_size": self.max_size,
            "exact_hits": exact_hits,
            "semantic_hits": semantic_hits,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }

    def reset_metrics(self, namespace: Optional[str] = None) -> None:
        """Reset counters/diagnostics without removing cached entries."""
        with self._lock:
            if namespace is None:
                self._counters.clear()
                self._last_lookup_by_namespace.clear()
                self._last_lookup_global = {}
            else:
                namespace_id = self._namespace_id(namespace)
                self._counters.pop(namespace_id, None)
                self._last_lookup_by_namespace.pop(namespace_id, None)

    def last_lookup(self, namespace: Optional[str] = None) -> dict:
        """Trả bản sao diagnostics gần nhất mà không lộ namespace gốc."""
        with self._lock:
            if namespace is None:
                return deepcopy(self._last_lookup_global)
            namespace_id = self._namespace_id(namespace)
            return deepcopy(self._last_lookup_by_namespace.get(namespace_id, {}))


_semantic_cache: Optional[SemanticCache] = None
_singleton_lock = RLock()


def get_semantic_cache() -> SemanticCache:
    global _semantic_cache
    if _semantic_cache is None:
        with _singleton_lock:
            if _semantic_cache is None:
                _semantic_cache = SemanticCache()
    return _semantic_cache
