"""Paired-check transformation following the published safe-transformation rules."""

import random
from typing import Any, Dict, Tuple

from jury_sim.fileio import load, save

SPACES = ("row_id", "doc_id", "invoice_id", "payment_id", "entity_id")


def _replace(obj: Any, m: Dict[str, str]) -> Any:
    if isinstance(obj, str):
        return m.get(obj, obj)
    if isinstance(obj, list):
        return [_replace(v, m) for v in obj]
    if isinstance(obj, dict):
        return {k: _replace(v, m) for k, v in obj.items()}
    return obj


def transform(src: str, dst: str, permute: bool, rename: bool, seed: int) -> Tuple[Dict[str, str], Dict[str, int]]:
    data = load(src)
    rng = random.Random(seed)
    mapping: Dict[str, str] = {}
    counts = {s: 0 for s in SPACES}
    if rename:
        ids = {s: set() for s in SPACES}
        for r in data["ledger"]:
            ids["row_id"].add(r["row_id"])
            for k in ("entity_id", "invoice_id", "payment_id"):
                if r[k] is not None:
                    ids[k].add(r[k])
        for d in data["invoices"]:
            ids["doc_id"].add(d["doc_id"]), ids["invoice_id"].add(d["invoice_id"]), ids["entity_id"].add(d["entity_id"])
        for p in data["payments"]:
            ids["doc_id"].add(p["doc_id"]), ids["payment_id"].add(p["payment_id"])
        for space, values in ids.items():
            fresh = [f"J{space[:3].upper()}{n:07d}" for n in range(len(values))]
            rng.shuffle(fresh)
            for old, new in zip(sorted(values), fresh):
                assert old not in mapping, "ID spaces overlap"
                mapping[old] = new
            counts[space] = len(values)
        for key in ("ledger", "invoices", "payments", "truth", "provenance"):
            data[key] = _replace(data[key], mapping)
        data["manifest"] = dict(data["manifest"], company=f"Jury Renamed Co {seed}")
    if permute:
        for key in ("ledger", "invoices", "payments", "fx"):
            rng.shuffle(data[key])
    save(dst, data)
    return mapping, counts
