"""Rendering: the only place where surface IDs, row order and text formats appear."""

from typing import Any, Dict, List, Tuple

from auditenv import canon, money, timeutil
from auditenv.books import PublicBooks, fx_key
from auditenv.config import ISSUE_TYPES
from auditenv.generator.model import ACCOUNTS, Books, LedgerLine, World
from auditenv.rng import Rng, derive_key
from auditenv.version import GENERATOR_VERSION, SCHEMA_VERSION

ISSUE_CONVENTIONS = {
    "duplicate_invoice": {
        "affected_ids": "doc_id of both invoice copies",
        "amount": "total_minor of the duplicated invoice",
        "currency": "invoice currency",
        "tolerance_minor": 0,
        "evidence": "both invoice doc_ids and one posting row_id (AR debit or revenue credit) of each copy",
    },
    "payment_allocation": {
        "affected_ids": "doc_id of the payment",
        "amount": "amount of that payment booked to 2400 Unapplied Customer Receipts",
        "currency": "ledger currency",
        "tolerance_minor": 0,
        "evidence": "payment doc_id and its 2400 Unapplied Customer Receipts row_id",
    },
}


class IdMap:
    def __init__(self, seed: int, world_hash: str, id_salt: int) -> None:
        self.key = derive_key("render-ids", seed, world_hash, id_salt)
        self.rng = Rng("render-numbering", seed, world_hash, id_salt)
        self.map: Dict[str, str] = {}
        self._used: Dict[str, str] = {}

    def assign(self, internal: str, surface: str) -> str:
        if internal in self.map:
            return self.map[internal]
        owner = self._used.get(surface)
        if owner is not None and owner != internal:
            raise AssertionError(f"surface id collision {surface}")
        self.map[internal] = surface
        self._used[surface] = internal
        return surface

    def token(self, kind: str, internal: str, length: int) -> str:
        return canon.keyed_token(self.key, f"{kind}|{internal}", length)

    def doc(self, internal: str) -> str:
        return self.assign(internal, "D" + self.token("doc", internal, 9))

    def row(self, internal: str) -> str:
        return self.assign(internal, "L" + self.token("row", internal, 10))

    def entity(self, internal: str) -> str:
        return self.assign(internal, "CUST-" + self.token("entity", internal, 6))


def render(world: World, books: Books, lines: List[LedgerLine], cfg: Dict[str, Any], world_hash: str) -> Tuple[PublicBooks, IdMap]:
    seed = world.seed
    render_cfg = cfg["render"]
    ids = IdMap(seed, world_hash, render_cfg["id_salt"])
    order_key = derive_key("render-order", seed, world_hash, render_cfg["order_salt"])
    shuffled = render_cfg["row_order"] == "shuffled"
    tz = world.tz
    base = world.base_currency
    year = world.period_start.year

    def order_token(internal: str) -> str:
        return canon.keyed_token(order_key, internal, 16)

    def ts(dt) -> str:
        return timeutil.iso(dt.astimezone(tz))

    # Business numbers: sequential by date, starting at a render-seeded base.
    first_issue: Dict[str, Any] = {}
    for inv in books.invoices:
        cur = first_issue.get(inv.number_key)
        if cur is None or inv.issued_at < cur:
            first_issue[inv.number_key] = inv.issued_at
    prefix = ids.rng.choice(("INV-", "SI-", "INV/", "FV-"))
    start = ids.rng.randint(100, 899)
    for n, number in enumerate(sorted(first_issue, key=lambda k: (first_issue[k], ids.token("num", k, 12)))):
        ids.assign(number, f"{prefix}{year}-{start + n:05d}")
    pay_prefix = ids.rng.choice(("PMT-", "RCPT-", "BR-"))
    pay_start = ids.rng.randint(1000, 8999)
    for n, pay in enumerate(sorted(books.payments, key=lambda p: (p.received_at, ids.token("pay", p.key, 12)))):
        ids.assign(f"pid:{pay.key}", f"{pay_prefix}{year}{pay_start + n:05d}")

    for cust in sorted(world.customers):
        ids.entity(cust)
    for inv in books.invoices:
        ids.doc(inv.key)
    for pay in books.payments:
        ids.doc(pay.key)
    for line in lines:
        ids.row(line.key)

    invoice_by_key = {inv.key: inv for inv in books.invoices}

    def doc_order(dt, internal: str):
        return (order_token(internal),) if shuffled else (dt, order_token(internal))

    invoices = []
    for inv in sorted(books.invoices, key=lambda i: doc_order(i.issued_at, i.key)):
        invoices.append({
            "doc_id": ids.map[inv.key],
            "invoice_id": ids.map[inv.number_key],
            "entity_id": ids.map[inv.customer_key],
            "issued_at": ts(inv.issued_at),
            "due_at": ts(inv.due_at),
            "currency": inv.currency,
            "total_minor": inv.total_minor,
            "line_items": [
                {
                    "line_no": n + 1,
                    "description": line.description,
                    "service_period": line.service_period,
                    "order_ref": line.order_ref,
                    "quantity": line.quantity,
                    "unit_price_minor": line.unit_price_minor,
                    "amount_minor": line.amount_minor,
                }
                for n, line in enumerate(inv.lines)
            ],
        })

    payments = []
    for pay in sorted(books.payments, key=lambda p: doc_order(p.received_at, p.key)):
        refs = None
        if pay.allocations is not None:
            refs = [
                {"invoice_id": ids.map[invoice_by_key[a.invoice_key].number_key], "amount_minor": a.amount_minor}
                for a in pay.allocations
            ]
        payments.append({
            "doc_id": ids.map[pay.key],
            "payment_id": ids.map[f"pid:{pay.key}"],
            "timestamp": ts(pay.received_at),
            "currency": pay.currency,
            "amount_minor": pay.amount_minor,
            "invoice_refs": refs,
        })

    if shuffled:
        ordered_lines = sorted(lines, key=lambda ln: order_token(ln.key))
    else:
        ordered_lines = sorted(lines, key=lambda ln: (ln.at, order_token(ln.entry_key), ln.seq))
    ledger = [
        {
            "row_id": ids.map[ln.key],
            "timestamp": ts(ln.at),
            "account": ln.account,
            "debit_minor": ln.debit_minor,
            "credit_minor": ln.credit_minor,
            "currency": base,
            "entity_id": ids.map[ln.entity_key] if ln.entity_key else None,
            "invoice_id": ids.map[ln.number_key] if ln.number_key else None,
            "payment_id": ids.map[f"pid:{ln.payment_key}"] if ln.payment_key else None,
        }
        for ln in ordered_lines
    ]

    fx = [
        {"date": d.isoformat(), "source_currency": cur, "target_currency": base, "rate": rate}
        for cur in sorted(world.fx)
        for d, rate in world.fx[cur]
    ]
    if shuffled:
        fx.sort(key=lambda r: order_token(fx_key(r)))

    currencies = sorted({base, *world.foreign_currencies})
    episode_id = "ep-" + ids.token("episode", "episode", 16).lower()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "generator_version": GENERATOR_VERSION,
        "company": world.company_name,
        "base_currency": base,
        "period_start": world.period_start.isoformat(),
        "period_end": world.period_end.isoformat(),
        "timezone": world.timezone,
        "utc_offset": timeutil.iso(world.opening_at)[-6:],
        "counts": {
            "ledger_rows": len(ledger),
            "invoices": len(invoices),
            "payments": len(payments),
            "documents": len(invoices) + len(payments),
            "fx_rates": len(fx),
        },
        "rounding": {"mode": world.rounding, "unit": "minor"},
        "currency_exponents": {c: money.EXPONENTS[c] for c in currencies},
        "money_format": "integer minor units; exponent per currency_exponents",
        "time_format": "ISO-8601 with UTC offset",
        "issue_types": list(ISSUE_TYPES),
        "files": ["manifest.json", "ledger.csv", "invoices.jsonl", "payments.jsonl", "fx.csv", "policy.json"],
    }
    policy = build_policy(world)
    return PublicBooks(manifest, policy, ledger, invoices, payments, fx), ids


def build_policy(world: World) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ledger": {
            "currency": world.base_currency,
            "accounts": dict(sorted((v, k) for k, v in ACCOUNTS.items())),
            "entries": "rows of one journal entry share the same timestamp",
            "null_references": "missing references are null; links are never guessed",
        },
        "fx": {
            "quote": "1 unit of source_currency = rate units of target_currency",
            "rate_date": "local date of the document in the company timezone",
            "missing_date": "use the latest published rate on or before that date",
            "conversion": "convert the document amount once and round to target minor units",
            "receipts": "AR is credited at the invoice carrying rate; the difference to cash at the "
                        "receipt-date rate is booked to 7900 Realized FX Gain/Loss",
        },
        "rounding": {"mode": world.rounding, "scope": "each conversion, to target minor units"},
        "period": {
            "start": world.period_start.isoformat(),
            "end": world.period_end.isoformat(),
            "timezone": world.timezone,
            "boundary": "inclusive local dates in the company timezone",
        },
        "allocation": {
            "rule": "receipts are applied to invoices exactly as listed in invoice_refs",
            "unreferenced": "receipts with null invoice_refs are held in 2400 Unapplied Customer Receipts",
        },
        "issue_conventions": ISSUE_CONVENTIONS,
    }
