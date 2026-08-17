from __future__ import annotations

from collections import Counter
from hashlib import blake2b
from math import sqrt
import re

_LATIN = re.compile(r"[a-zA-Z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    text = text.lower().strip()
    out = _LATIN.findall(text)
    for block in _CJK.findall(text):
        if len(block) <= 5:
            out.append(block)
        out.extend(block[i:i+2] for i in range(max(0, len(block)-1)))
        out.extend(block[i:i+3] for i in range(max(0, len(block)-2)))
    return [x for x in out if x]


def _bucket(feature: str, dims: int) -> tuple[int, float]:
    digest = blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % dims, 1.0 if ((value >> 9) & 1) else -1.0


def hashed_vector(text: str, *, dims: int = 256) -> dict[int, float]:
    feats = Counter(tokenize(text))
    compact = re.sub(r"\s+", "", text.lower())
    for n in (2, 3, 4):
        for i in range(max(0, len(compact)-n+1)):
            feats[f"g{n}:{compact[i:i+n]}"] += 0.35
    vec: dict[int, float] = {}
    for feat, weight in feats.items():
        idx, sign = _bucket(feat, dims)
        vec[idx] = vec.get(idx, 0.0) + sign * float(weight)
    norm = sqrt(sum(v*v for v in vec.values())) or 1.0
    return {k: v/norm for k, v in vec.items()}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v*b.get(k, 0.0) for k, v in a.items())
