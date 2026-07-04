"""Vector retrieval helpers for YCR."""

from __future__ import annotations

import re

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    width = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(width))
