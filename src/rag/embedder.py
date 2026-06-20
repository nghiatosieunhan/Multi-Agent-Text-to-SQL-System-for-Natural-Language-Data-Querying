"""
Embedding Service — dùng Google Gemini gemini-embedding-001.
Fallback: hashing vectors cố định khi Gemini không khả dụng.
"""
import hashlib
import math
import re
import time
import unicodedata
from typing import Optional

import structlog
from src.config import config

log = structlog.get_logger("embedder")

# ── Lazy client init ───────────────────────────────────────────────────────────
_gemini_client: Optional[object] = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        from google import genai
        _gemini_client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        return _gemini_client
    except ImportError:
        log.warning("google-genai not installed — using TF-IDF fallback")
        return None
    except Exception as e:
        log.warning("Gemini client init failed", error=str(e))
        return None


def embed_with_retry(
    texts: list[str],
    model: Optional[str] = None,
    max_retries: int = 3,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """
    Batch embedding với exponential backoff retry.
    Dùng Gemini gemini-embedding-001, fallback sang hashing vectors.
    """
    embeddings, _ = embed_with_retry_metadata(
        texts,
        model=model,
        max_retries=max_retries,
        task_type=task_type,
    )
    return embeddings


def embed_with_retry_metadata(
    texts: list[str],
    model: Optional[str] = None,
    max_retries: int = 3,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> tuple[list[list[float]], dict]:
    """Embed văn bản và trả kèm backend/model/số chiều để kiểm tra tương thích."""
    if not texts:
        return [], {"backend": "none", "model": "none", "dimension": 0}

    model = model or config.EMBEDDING_MODEL
    client = _get_gemini_client()

    # Gemini embedding
    if client is not None:
        from google.genai import types

        # Build content list (Part objects)
        contents = []
        for t in texts:
            contents.append(types.Part.from_text(text=t))

        for attempt in range(max_retries):
            try:
                resp = client.models.embed_content(
                    model=model,
                    contents=contents,
                    config={"task_type": task_type},
                )
                embeddings = [e.values for e in resp.embeddings]
                log.info("gemini_embed_success", count=len(texts), attempt=attempt + 1)
                dimension = len(embeddings[0]) if embeddings else 0
                return embeddings, {
                    "backend": "gemini",
                    "model": model,
                    "dimension": dimension,
                }

            except Exception as e:
                err = str(e)
                if ("429" in err or "RESOURCE_EXHAUSTED" in err) and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    log.warning("gemini_embed_rate_limit", attempt=attempt + 1, wait_s=wait)
                    time.sleep(wait)
                else:
                    log.error("gemini_embed_failed", error=err, attempt=attempt + 1)
                    break

        log.warning("gemini_embed_fallback_to_hashing", model=model)

    embeddings = _fallback_embed(texts)
    dimension = len(embeddings[0]) if embeddings else 0
    return embeddings, {
        "backend": "hashing",
        "model": "stable-token-hashing-v1",
        "dimension": dimension,
    }


def embed_single(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed một câu đơn lẻ."""
    embeddings = embed_with_retry([text], task_type=task_type)
    return embeddings[0] if embeddings else []


def embed_single_with_metadata(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> tuple[list[float], dict]:
    """Embed một câu và trả metadata của backend thực tế đã sử dụng."""
    embeddings, metadata = embed_with_retry_metadata([text], task_type=task_type)
    return (embeddings[0] if embeddings else []), metadata


def _fallback_embed(texts: list[str]) -> list[list[float]]:
    """
    Vector hashing cố định, có thể so sánh giữa các lần gọi độc lập.

    Không dùng TF-IDF fit riêng từng batch vì vocabulary/column index thay đổi,
    khiến cosine similarity giữa hai lần embed không còn ý nghĩa.
    """
    dimension = 384
    vectors = []

    for text in texts:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vector = [0.0] * dimension

        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimension
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        vectors.append(vector)

    log.info("hashing_fallback_embed", count=len(texts), dimension=dimension)
    return vectors
