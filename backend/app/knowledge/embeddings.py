"""Pluggable lightweight embedding helpers for incident memory retrieval."""

from __future__ import annotations

import hashlib
import math
import os
import re


class EmbeddingProvider:
    """Small provider interface used by the knowledge store.

    The default provider is disabled so OpsGuard does not require external
    model infrastructure. ``local_hash`` is deterministic and dependency-free;
    it is useful as a semantic rerank fallback and for regression tests.
    """

    name = "disabled"

    def embed(self, text: str) -> list[float]:
        return []


class DisabledEmbeddingProvider(EmbeddingProvider):
    name = "disabled"


class LocalHashEmbeddingProvider(EmbeddingProvider):
    name = "local_hash"

    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 else -1.0
            vector[index] += sign
        return _normalize(vector)


def get_embedding_provider() -> EmbeddingProvider:
    provider = os.getenv("OPSGUARD_EMBEDDING_PROVIDER", "disabled").strip().lower()
    if provider in {"local", "local_hash", "hash"}:
        return LocalHashEmbeddingProvider()
    return DisabledEmbeddingProvider()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]{1,4}", (text or "").lower())


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]
