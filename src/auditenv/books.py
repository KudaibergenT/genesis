"""Open episode files: in-memory form, writer and loader."""

import csv
import io
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from auditenv import canon

LEDGER_COLUMNS = ("row_id", "timestamp", "account", "debit_minor", "credit_minor", "currency",
                  "entity_id", "invoice_id", "payment_id")
FX_COLUMNS = ("date", "source_currency", "target_currency", "rate")
LEDGER_INT_COLUMNS = ("debit_minor", "credit_minor")
LEDGER_NULLABLE = ("entity_id", "invoice_id", "payment_id")

TABLES = ("ledger", "invoices", "payments", "fx")
PRIMARY_KEYS = {"ledger": "row_id", "invoices": "doc_id", "payments": "doc_id", "fx": "fx_key"}


def fx_key(row: Dict[str, Any]) -> str:
    return f"{row['date']}|{row['source_currency']}|{row['target_currency']}"


@dataclass
class PublicBooks:
    manifest: Dict[str, Any]
    policy: Dict[str, Any]
    ledger: List[Dict[str, Any]]
    invoices: List[Dict[str, Any]]
    payments: List[Dict[str, Any]]
    fx: List[Dict[str, Any]]

    def tables(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"ledger": self.ledger, "invoices": self.invoices, "payments": self.payments, "fx": self.fx}


def _csv_text(rows: List[Dict[str, Any]], columns) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row[c] is None else row[c] for c in columns])
    return buf.getvalue()


def _jsonl_text(rows: List[Dict[str, Any]]) -> str:
    return "".join(canon.dumps(r) + "\n" for r in rows)


def books_files(books: PublicBooks) -> Dict[str, str]:
    return {
        "manifest.json": json.dumps(books.manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        "policy.json": json.dumps(books.policy, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        "ledger.csv": _csv_text(books.ledger, LEDGER_COLUMNS),
        "invoices.jsonl": _jsonl_text(books.invoices),
        "payments.jsonl": _jsonl_text(books.payments),
        "fx.csv": _csv_text(books.fx, FX_COLUMNS),
    }


def write_books(books: PublicBooks, directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for name, text in books_files(books).items():
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def load_books(directory: str) -> PublicBooks:
    def read(name: str) -> str:
        with open(os.path.join(directory, name), encoding="utf-8", newline="") as fh:
            return fh.read()

    def read_csv(name: str) -> List[Dict[str, Any]]:
        return list(csv.DictReader(io.StringIO(read(name))))

    ledger = []
    for row in read_csv("ledger.csv"):
        for col in LEDGER_INT_COLUMNS:
            row[col] = int(row[col])
        for col in LEDGER_NULLABLE:
            row[col] = row[col] or None
        ledger.append(row)
    return PublicBooks(
        manifest=json.loads(read("manifest.json")),
        policy=json.loads(read("policy.json")),
        ledger=ledger,
        invoices=[json.loads(line) for line in read("invoices.jsonl").splitlines() if line],
        payments=[json.loads(line) for line in read("payments.jsonl").splitlines() if line],
        fx=read_csv("fx.csv"),
    )
