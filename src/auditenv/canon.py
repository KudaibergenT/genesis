"""Canonical JSON serialization and hashing (sorted keys, no NaN, compact)."""

import base64
import hashlib
import hmac
import json
from typing import Any


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(dumps(obj))


def json_copy(obj: Any) -> Any:
    """Deep copy through JSON: guarantees JSON-compatibility and no shared references."""
    return json.loads(dumps(obj))


def keyed_token(key: bytes, message: str, length: int) -> str:
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:length]
