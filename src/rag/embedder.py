"""
Embedding Service — dùng Google Gemini gemini-embedding-001.
Fallback: TF-IDF khi không có Gemini API key.
"""
import time
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
) -> list[list[float]]:
    """
    Batch embedding với exponential backoff retry.
    Dùng Gemini gemini-embedding-001, fallback sang TF-IDF.
    """
    if not texts:
        return []

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
                    config={"task_type": "RETRIEVAL_DOCUMENT"},
                )
                embeddings = [e.values for e in resp.embeddings]
                log.info("gemini_embed_success", count=len(texts), attempt=attempt + 1)
                return embeddings

            except Exception as e:
                err = str(e)
                if ("429" in err or "RESOURCE_EXHAUSTED" in err) and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    log.warning("gemini_embed_rate_limit", attempt=attempt + 1, wait_s=wait)
                    time.sleep(wait)
                else:
                    log.error("gemini_embed_failed", error=err, attempt=attempt + 1)
                    break

        log.warning("gemini_embed_fallback_to_tfidf", model=model)

    # Fallback: TF-IDF
    return _fallback_embed(texts)


def embed_single(text: str) -> list[float]:
    """Embed một câu đơn lẻ."""
    embeddings = embed_with_retry([text])
    return embeddings[0] if embeddings else []


def _fallback_embed(texts: list[str]) -> list[list[float]]:
    """
    TF-IDF fallback khi không dùng được Gemini API.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        import numpy as np

        vectorizer = TfidfVectorizer(max_features=384)
        matrix = vectorizer.fit_transform(texts).toarray()

        # Pad/truncate to fixed dimension
        target_dim = 384
        if matrix.shape[1] < target_dim:
            padded = np.zeros((matrix.shape[0], target_dim))
            padded[:, :matrix.shape[1]] = matrix
            matrix = padded
        elif matrix.shape[1] > target_dim:
            matrix = matrix[:, :target_dim]

        log.info("tfidf_fallback_embed", count=len(texts))
        return matrix.tolist()

    except ImportError:
        # Final fallback: deterministic hash vectors
        import hashlib
        import numpy as np

        vectors = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = np.array([b / 255.0 for b in (list(h) * 16)[:384]])
            vectors.append(vec.tolist())
        log.warning("hash_fallback_embed", count=len(texts))
        return vectors