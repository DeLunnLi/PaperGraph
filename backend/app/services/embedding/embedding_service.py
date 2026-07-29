from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

_CACHE_MAX = 512
_CACHE_TTL_SEC = 900.0
_MAX_INPUT_CHARS = 6000
_EMBED_BATCH_SIZE = 16
_cache: OrderedDict[tuple[str, str], tuple[float, list[float]]] = OrderedDict()
_cache_lock = threading.Lock()


def _config() -> tuple[str, str, str, float]:
    # Settings loads backend/.env consistently in application and standalone tests.
    from ...settings import get_settings

    settings = get_settings()
    api_key = (
        os.getenv("EMBED_API_KEY")
        or settings.embed_api_key
        or os.getenv("LLM_API_KEY")
        or settings.openai_api_key
        or ""
    ).strip()
    base_url = (
        os.getenv("EMBED_BASE_URL")
        or settings.embed_base_url
        or os.getenv("LLM_BASE_URL")
        or settings.openai_base_url
        or ""
    ).strip().rstrip("/")
    model = (os.getenv("EMBED_MODEL_NAME") or settings.embed_model_name or "").strip()
    try:
        raw_timeout = os.getenv("EMBED_TIMEOUT_SEC") or settings.embed_timeout_sec
        timeout = max(1.0, min(30.0, float(raw_timeout or 8)))
    except (TypeError, ValueError):
        timeout = 8.0
    return api_key, base_url, model, timeout


def embedding_enabled() -> bool:
    api_key, base_url, model, _ = _config()
    return bool(api_key and base_url and model)


def _endpoint(base_url: str) -> str:
    return base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings"


def _normalize_input(text: str) -> str:
    return " ".join(str(text or "").split())[:_MAX_INPUT_CHARS]


def _cache_get(model: str, text: str) -> list[float] | None:
    key = (model, text)
    now = time.monotonic()
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        created, vector = item
        if now - created > _CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return vector


def _cache_put(model: str, text: str, vector: list[float]) -> None:
    key = (model, text)
    with _cache_lock:
        _cache[key] = (time.monotonic(), vector)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    """Embed texts through an OpenAI-compatible endpoint, preserving input order."""
    normalized = [_normalize_input(text) for text in texts]
    if not normalized:
        return []
    if any(not text for text in normalized):
        raise ValueError("embedding_input_empty")

    api_key, base_url, model, timeout = _config()
    if not (api_key and base_url and model):
        raise RuntimeError("embedding_not_configured")

    output: list[list[float] | None] = [None] * len(normalized)
    missing: list[str] = []
    missing_positions: dict[str, list[int]] = {}
    for index, text in enumerate(normalized):
        cached = _cache_get(model, text)
        if cached is not None:
            output[index] = cached
            continue
        if text not in missing_positions:
            missing.append(text)
            missing_positions[text] = []
        missing_positions[text].append(index)

    if missing:
        trust_env = os.getenv("LLM_DISABLE_PROXY", "0").strip().lower() not in {"1", "true", "yes", "on"}
        payload = None
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            for batch_start in range(0, len(missing), _EMBED_BATCH_SIZE):
                batch = missing[batch_start : batch_start + _EMBED_BATCH_SIZE]
                batch_positions = {
                    i: missing_positions[missing[batch_start + i]]
                    for i in range(len(batch))
                }
                for attempt in range(2):
                    response = client.post(
                        _endpoint(base_url),
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": model, "input": batch},
                    )
                    response.raise_for_status()
                    try:
                        payload = response.json()
                        break
                    except ValueError:
                        if attempt:
                            raise
                        logger.warning("embedding_response_json_retry batch_start=%s", batch_start)
                rows = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(rows, list) or len(rows) != len(batch):
                    raise ValueError("embedding_response_count_mismatch")
                vectors_by_index: dict[int, list[float]] = {}
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("embedding_response_invalid")
                    index = row.get("index")
                    vector = row.get("embedding")
                    if not isinstance(index, int) or not isinstance(vector, list) or not vector:
                        raise ValueError("embedding_response_invalid")
                    parsed = [float(value) for value in vector]
                    if not all(math.isfinite(value) for value in parsed):
                        raise ValueError("embedding_response_non_finite")
                    vectors_by_index[index] = parsed
                if set(vectors_by_index) != set(range(len(batch))):
                    raise ValueError("embedding_response_index_mismatch")
                dimensions = {len(vector) for vector in vectors_by_index.values()}
                if len(dimensions) != 1:
                    raise ValueError("embedding_response_dimension_mismatch")
                for batch_idx, text in enumerate(batch):
                    vector = vectors_by_index[batch_idx]
                    _cache_put(model, text, vector)
                    for position in batch_positions[batch_idx]:
                        output[position] = vector
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(missing):
            raise ValueError("embedding_response_count_mismatch")
        vectors_by_index: dict[int, list[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("embedding_response_invalid")
            index = row.get("index")
            vector = row.get("embedding")
            if not isinstance(index, int) or not isinstance(vector, list) or not vector:
                raise ValueError("embedding_response_invalid")
            parsed = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in parsed):
                raise ValueError("embedding_response_non_finite")
            vectors_by_index[index] = parsed
        if set(vectors_by_index) != set(range(len(missing))):
            raise ValueError("embedding_response_index_mismatch")
        dimensions = {len(vector) for vector in vectors_by_index.values()}
        if len(dimensions) != 1:
            raise ValueError("embedding_response_dimension_mismatch")
        for missing_index, text in enumerate(missing):
            vector = vectors_by_index[missing_index]
            _cache_put(model, text, vector)
            for position in missing_positions[text]:
                output[position] = vector

    if any(vector is None for vector in output):
        raise ValueError("embedding_response_incomplete")
    return [vector for vector in output if vector is not None]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))
