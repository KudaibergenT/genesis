"""Error operators. Each mutates documents/postings only (never the event history it
was derived from), keeps every entry balanced, and records its causal change."""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from auditenv import money
from auditenv.generator.model import Books, Invoice, TimeAllocator, World
from auditenv.generator.world import _business_time, _nice_part
from auditenv.rng import Rng

DUPLICATE_TOLERANCE_MINOR = 0
ALLOCATION_TOLERANCE_MINOR = 0


@dataclass
class InternalIssue:
    key: str
    type: str
    variant: str
    affected: Tuple[str, ...]
    currency: str
    tolerance_minor: int
    evidence_sets: List[Tuple[str, ...]]
    amount_minor: Optional[int] = None
    amount_from_line: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


def inject_duplicate_invoices(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                              alloc: TimeAllocator, used: Set[str]) -> List[InternalIssue]:
    candidates = sorted(
        (inv for inv in books.invoices if inv.key not in used and not inv.key.startswith("inv#dup")),
        key=lambda inv: inv.key,
    )
    issues: List[InternalIssue] = []
    for k, orig in enumerate(rng.sample(candidates, min(count, len(candidates)))):
        variant = rng.choice(variants)
        orig_day = world.local_date(orig.issued_at)
        day = min(orig_day + timedelta(days=rng.randint(0, 6)), world.period_end)
        at = _business_time(rng, day, world.tz)
        if at <= orig.issued_at:
            at = orig.issued_at + timedelta(minutes=rng.randint(3, 90))
        at = alloc.take(at)
        if not world.in_period(at):
            at = alloc.take(orig.issued_at + timedelta(seconds=rng.randint(30, 600)))
        dup = Invoice(
            key=f"inv#dup{k}",
            number_key=orig.number_key if variant == "same_number" else f"num#dup{k}",
            customer_key=orig.customer_key,
            issued_at=at,
            due_at=at + (orig.due_at - orig.issued_at),
            currency=orig.currency,
            lines=orig.lines,
        )
        books.invoices.append(dup)
        used.add(orig.key)
        posting_rows = lambda inv: (f"E:{inv.key}/ar", f"E:{inv.key}/rev")  # noqa: E731
        evidence = [
            tuple(sorted((orig.key, dup.key, a, b)))
            for a in posting_rows(orig)
            for b in posting_rows(dup)
        ]
        issues.append(
            InternalIssue(
                key=f"issue:duplicate_invoice:{k}",
                type="duplicate_invoice",
                variant=variant,
                affected=tuple(sorted((orig.key, dup.key))),
                currency=orig.currency,
                tolerance_minor=DUPLICATE_TOLERANCE_MINOR,
                evidence_sets=evidence,
                amount_minor=orig.total_minor,
                params={
                    "source_document": orig.key,
                    "created_document": dup.key,
                    "created_entry": f"E:{dup.key}",
                    "number": "shared" if variant == "same_number" else "new",
                },
            )
        )
    return issues


def inject_payment_allocation(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                              used: Set[str]) -> List[InternalIssue]:
    candidates = sorted(
        (
            p for p in books.payments
            if p.allocations is not None
            and p.key not in books.allocation_overrides
            and not any(a.invoice_key in used for a in p.allocations)
        ),
        key=lambda p: p.key,
    )
    issues: List[InternalIssue] = []
    for k, pay in enumerate(rng.sample(candidates, min(count, len(candidates)))):
        variant = rng.choice(variants)
        target = rng.choice(list(pay.allocations or ()))
        applied = 0
        if variant == "partial":
            part = _nice_part(rng, target.amount_minor, pay.currency)
            if part is None:
                variant = "missing"
            else:
                applied = part
        unapplied = target.amount_minor - applied
        if money.convert(unapplied, pay.currency, world.base_currency,
                         world.rate(pay.currency, world.local_date(pay.received_at)), world.rounding) <= 0:
            continue
        books.allocation_overrides[pay.key] = {target.invoice_key: applied}
        used.update(a.invoice_key for a in pay.allocations or ())
        issues.append(
            InternalIssue(
                key=f"issue:payment_allocation:{k}",
                type="payment_allocation",
                variant=variant,
                affected=(pay.key,),
                currency=world.base_currency,
                tolerance_minor=ALLOCATION_TOLERANCE_MINOR,
                evidence_sets=[tuple(sorted((pay.key, f"E:{pay.key}/unapplied")))],
                amount_from_line=f"E:{pay.key}/unapplied",
                params={
                    "payment": pay.key,
                    "invoice": target.invoice_key,
                    "remittance_amount_minor": target.amount_minor,
                    "applied_amount_minor": applied,
                    "unapplied_amount_minor_payment_currency": unapplied,
                },
            )
        )
    return issues
