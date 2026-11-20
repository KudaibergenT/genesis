"""Read and write episode directories using only the documented file formats."""

import csv
import io
import json
import os
from typing import Any, Dict, List

NULL = "null"
LEDGER_COLUMNS = ["row_id", "timestamp", "account", "debit_minor", "credit_minor", "currency", "entity_id",
                  "invoice_id", "payment_id"]
LEDGER_REFS = ("entity_id", "invoice_id", "payment_id")
FX_COLUMNS = ["date", "source_currency", "target_currency", "rate"]


def _read(d: str, name: str) -> str:
    with open(os.path.join(d, name), encoding="utf-8", newline="") as fh:
        return fh.read()


def load(d: str, with_closed: bool = True) -> Dict[str, Any]:
    ledger = []
    for row in csv.DictReader(io.StringIO(_read(d, "ledger.csv"))):
        row["debit_minor"], row["credit_minor"] = int(row["debit_minor"]), int(row["credit_minor"])
        for k in LEDGER_REFS:
            row[k] = None if row[k] == NULL else row[k]
        ledger.append(row)
    data = {
        "manifest": json.loads(_read(d, "manifest.json")),
        "policy": json.loads(_read(d, "policy.json")),
        "ledger": ledger,
        "invoices": [json.loads(x) for x in _read(d, "invoices.jsonl").splitlines() if x],
        "payments": [json.loads(x) for x in _read(d, "payments.jsonl").splitlines() if x],
        "fx": list(csv.DictReader(io.StringIO(_read(d, "fx.csv")))),
        "raw_csv": {"ledger.csv": _read(d, "ledger.csv"), "fx.csv": _read(d, "fx.csv")},
    }
    if with_closed:
        data["truth"] = json.loads(_read(d, "truth.json"))
        data["provenance"] = json.loads(_read(d, "provenance.json"))
    return data


def save(d: str, data: Dict[str, Any]) -> None:
    os.makedirs(d, exist_ok=True)
    for name in ("manifest", "policy", "truth", "provenance"):
        with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(data[name], fh, ensure_ascii=False)
    for name, rows, columns in (("ledger.csv", data["ledger"], LEDGER_COLUMNS), ("fx.csv", data["fx"], FX_COLUMNS)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(columns)
            for r in rows:
                writer.writerow([NULL if r[c] is None else r[c] for c in columns])
    for name in ("invoices", "payments"):
        with open(os.path.join(d, f"{name}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data[name]))


def oracle_findings(truth: Dict[str, Any]) -> Dict[str, Any]:
    return {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": "oracle"}
        for i in truth["issues"]]}


def fx_id(row: Dict[str, Any]) -> str:
    return f"{row['date']}|{row['source_currency']}|{row['target_currency']}"


def all_ids(data: Dict[str, Any]) -> List[str]:
    ids = [r["row_id"] for r in data["ledger"]] + [d["doc_id"] for d in data["invoices"] + data["payments"]]
    return ids + [fx_id(r) for r in data["fx"]]
