"""Unit tests cho SemanticCache, không gọi API embedding bên ngoài."""

import pytest

import src.memory.semantic_cache as cache_module
from src.memory.semantic_cache import SemanticCache
from src.rag.embedder import _fallback_embed


def _metadata(backend="fake", model="fake-v1", dimension=3):
    return {"backend": backend, "model": model, "dimension": dimension}


@pytest.fixture
def fake_embed(monkeypatch):
    calls = []

    def embed(text, task_type="RETRIEVAL_DOCUMENT"):
        calls.append((text, task_type))
        return [1.0, 0.01, 0.0], _metadata()

    monkeypatch.setattr(cache_module, "embed_single_with_metadata", embed)
    return calls


def test_first_lookup_misses_then_exact_lookup_hits_without_embedding(fake_embed):
    cache = SemanticCache(max_size=10)
    question = "Hiển thị 5 đơn hàng đầu tiên?"
    result = {"rows": [{"OrderID": 1}], "row_count": 1}

    assert cache.get(question, namespace="orders.db") is None
    cache.put(question, result, "SELECT * FROM orders LIMIT 5", namespace="orders.db")
    calls_before_exact_get = len(fake_embed)

    cached_result, _ = cache.get(
        "  HIỂN THỊ 5 đơn hàng đầu tiên!!! ",
        namespace="orders.db",
    )

    assert len(fake_embed) == calls_before_exact_get
    assert cached_result["from_cache"] is True
    assert cache.stats("orders.db") == {
        "size": 1,
        "max_size": 10,
        "exact_hits": 1,
        "semantic_hits": 0,
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
    }
    assert cache.last_lookup("orders.db")["status"] == "exact_hit"


def test_semantic_variant_hits_when_all_guards_pass(fake_embed):
    cache = SemanticCache(threshold=0.92, jaccard_threshold=0.65)
    cache.put(
        "Hiển thị 5 đơn hàng đầu tiên",
        {"rows": [{"OrderID": 1}]},
        "SELECT * FROM orders LIMIT 5",
        namespace="orders.db",
    )

    cached = cache.get("Liệt kê 5 đơn hàng đầu tiên", namespace="orders.db")

    assert cached is not None
    assert cache.stats("orders.db")["semantic_hits"] == 1
    diagnostic = cache.last_lookup("orders.db")
    assert diagnostic["status"] == "semantic_hit"
    assert diagnostic["cosine"] >= 0.92
    assert diagnostic["jaccard"] >= 0.65


@pytest.mark.parametrize(
    "question",
    [
        "Liệt kê 10 đơn hàng đầu tiên",
        "Liệt kê 5 đơn hàng cuối cùng",
    ],
)
def test_critical_tokens_force_miss(question, fake_embed):
    cache = SemanticCache(threshold=0.92, jaccard_threshold=0.0)
    cache.put(
        "Hiển thị 5 đơn hàng đầu tiên",
        {"rows": []},
        "SELECT * FROM orders LIMIT 5",
        namespace="orders.db",
    )

    assert cache.get(question, namespace="orders.db") is None
    assert cache.last_lookup("orders.db")["reason"] == "critical_token_mismatch"


def test_low_jaccard_reports_intent_rejection(fake_embed):
    cache = SemanticCache(threshold=0.92, jaccard_threshold=0.65)
    cache.put(
        "Hiển thị 5 đơn hàng đầu tiên",
        {"rows": []},
        "SELECT * FROM orders LIMIT 5",
        namespace="orders.db",
    )

    assert cache.get("Liệt kê 5 khách hàng đầu tiên", namespace="orders.db") is None
    assert cache.last_lookup("orders.db")["reason"] == "jaccard_below_threshold"


def test_cache_is_isolated_by_database_namespace(fake_embed):
    cache = SemanticCache()
    question = "Hiển thị 5 đơn hàng đầu tiên"
    cache.put(question, {"database": "A"}, "SELECT 1", namespace="a.db")

    assert cache.get(question, namespace="b.db") is None
    assert cache.stats("a.db")["size"] == 1
    assert cache.stats("b.db")["size"] == 0


def test_embedding_backend_change_reembeds_cached_question(monkeypatch):
    calls = []

    def changing_embed(text, task_type="RETRIEVAL_DOCUMENT"):
        calls.append((text, task_type))
        if len(calls) == 1:
            return [1.0, 0.0, 0.0], _metadata("gemini", "gemini-test")
        return [1.0, 0.0, 0.0], _metadata("hashing", "hashing-test")

    monkeypatch.setattr(cache_module, "embed_single_with_metadata", changing_embed)
    cache = SemanticCache(threshold=0.92, jaccard_threshold=0.65)
    cache.put(
        "Hiển thị 5 đơn hàng đầu tiên",
        {"rows": []},
        "SELECT * FROM orders LIMIT 5",
    )

    assert cache.get("Liệt kê 5 đơn hàng đầu tiên") is not None
    assert len(calls) == 3
    assert cache.last_lookup()["status"] == "semantic_hit"


def test_hashing_fallback_vectors_are_comparable_between_calls():
    first = _fallback_embed(["hiển thị năm đơn hàng"])[0]
    repeated = _fallback_embed(["hiển thị năm đơn hàng"])[0]
    unrelated = _fallback_embed(["doanh thu sản phẩm theo tháng"])[0]

    assert SemanticCache._cosine_similarity(first, repeated) == pytest.approx(1.0)
    assert SemanticCache._cosine_similarity(first, unrelated) < 0.5


def test_cached_result_is_returned_as_a_copy(fake_embed):
    cache = SemanticCache()
    original = {"rows": [{"OrderID": 1}]}
    cache.put("Một câu hỏi", original, "SELECT 1")

    first_result, _ = cache.get("Một câu hỏi")
    first_result["rows"][0]["OrderID"] = 999
    second_result, _ = cache.get("Một câu hỏi")

    assert second_result["rows"][0]["OrderID"] == 1
