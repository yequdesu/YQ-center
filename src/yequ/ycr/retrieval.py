"""Deterministic dense-vector retrieval for YCR chunks."""

from __future__ import annotations

import hashlib
import math
import re

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def dense_embedding(text: str, *, dimensions: int = 256) -> list[float]:
    vector = [0.0] * dimensions
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    width = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(width))
