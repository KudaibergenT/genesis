"""Deterministic PRNG built on SHA-256 in counter mode.

Python's `random` only guarantees `random()` across versions and NumPy streams are
build-dependent, so every draw here is integer-only and derived from hashlib.
"""

import hashlib
from typing import List, Sequence, Tuple, TypeVar

T = TypeVar("T")

_SEP = "\x1f"


def derive_key(*parts: object) -> bytes:
    material = _SEP.join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(material).digest()


class Rng:
    def __init__(self, *key_parts: object) -> None:
        self._key = derive_key(*key_parts)
        self._counter = 0
        self._buf = b""

    def child(self, *parts: object) -> "Rng":
        return Rng(self._key.hex(), *parts)

    def _take(self, n: int) -> bytes:
        while len(self._buf) < n:
            block = hashlib.sha256(self._key + self._counter.to_bytes(8, "big")).digest()
            self._counter += 1
            self._buf += block
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def u64(self) -> int:
        return int.from_bytes(self._take(8), "big")

    def randbelow(self, n: int) -> int:
        if n <= 0:
            raise ValueError("randbelow requires n > 0")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            x = self.u64()
            if x < limit:
                return x % n

    def randint(self, lo: int, hi: int) -> int:
        if hi < lo:
            raise ValueError("randint requires lo <= hi")
        return lo + self.randbelow(hi - lo + 1)

    def chance(self, per_mille: int) -> bool:
        return self.randbelow(1000) < per_mille

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("choice from empty sequence")
        return seq[self.randbelow(len(seq))]

    def weighted(self, items: Sequence[Tuple[T, int]]) -> T:
        total = sum(w for _, w in items)
        r = self.randbelow(total)
        for item, w in items:
            if r < w:
                return item
            r -= w
        raise AssertionError("unreachable")

    def shuffle(self, items: List[T]) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.randbelow(i + 1)
            items[i], items[j] = items[j], items[i]

    def sample(self, seq: Sequence[T], k: int) -> List[T]:
        pool = list(seq)
        self.shuffle(pool)
        return pool[:k]


def resolve_range(value: object, rng: Rng) -> int:
    """Config values are either an int or an inclusive [lo, hi] range."""
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid count")
    if isinstance(value, int):
        return value
    lo, hi = value  # type: ignore[misc]
    return rng.randint(int(lo), int(hi))
