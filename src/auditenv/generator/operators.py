"""Error operators. Each changes documents/bookings only (never the event history it
was derived from), keeps every entry balanced, and records its causal change.

Booking-level errors (FX, cut-off, allocation) are declarative overrides consumed by
`build_ledger`; duplicate invoices and the interaction receipt add documents.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from auditenv import money
from auditenv.generator.ledger import booked_invoice_conversion, invoice_policy_rate, receipt_policy_rate
from auditenv.generator.model import Allocation, Books, Invoice, Payment, TimeAllocator, World
from auditenv.generator.world import _business_time, _nice_part, _sig_round, _time_between
from auditenv.rng import Rng
from auditenv.timeutil import ZONE_OFFSETS_MINUTES

DUPLICATE_TOLERANCE_MINOR = 0
ALLOCATION_TOLERANCE_MINOR = 0
FX_TOLERANCE_MINOR = 1
CUTOFF_TOLERANCE_MINOR = 0


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


# ---------------------------------------------------------------------------- helpers
def _is_regular_invoice(world: World, inv: Invoice) -> bool:
    return inv.kind == "invoice" and not inv.key.startswith("inv#dup") and world.in_period(inv.issued_at)


def _is_regular_receipt(world: World, pay: Payment) -> bool:
    return pay.amount_minor > 0 and world.in_period(pay.received_at) and not pay.key.startswith("pay#ix")


def _paid_invoice_keys(books: Books) -> Set[str]:
    return {a.invoice_key for p in books.payments for a in (p.allocations or ())}


def _fx_threshold(world: World, correct: int) -> int:
    """Smallest FX discrepancy worth injecting: far above the published tolerance."""
    return max(20 * FX_TOLERANCE_MINOR, abs(correct) // 1000)


def _perturbed_rate(rng: Rng, rate: str) -> str:
    sign = rng.choice((-1, 1))
    factor = Decimal(1000 + sign * rng.randint(8, 40)) / Decimal(1000)
    return format(_sig_round(Decimal(rate) * factor, 6), "f")


def _other_date_rate(rng: Rng, world: World, currency: str, local_day, policy_rate: str) -> Optional[Tuple[str, str]]:
    options = [(d, r) for d, r in world.fx[currency] if r != policy_rate and 1 <= abs((d - local_day).days) <= 10]
    if not options:
        return None
    d, r = rng.choice(options)
    return r, d.isoformat()


def _invoice_posting_evidence(inv: Invoice, *extra: str) -> List[Tuple[str, ...]]:
    return [tuple(sorted((inv.key, f"E:{inv.key}/{tag}") + extra)) for tag in ("ar", "rev")]


def _utc_wall_as_local(dt: datetime, world: World) -> datetime:
    """The classic time-zone bug: a UTC wall-clock time stamped with the local offset."""
    utc = dt.astimezone(timezone.utc)
    return datetime.combine(utc.date(), utc.time(), world.tz)


# ---------------------------------------------------------------------------- duplicates
def inject_duplicate_invoices(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                              alloc: TimeAllocator, used: Set[str]) -> List[InternalIssue]:
    candidates = sorted((inv for inv in books.invoices if inv.key not in used and _is_regular_invoice(world, inv)),
                        key=lambda inv: inv.key)
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
        used.update((orig.key, dup.key))
        posting_rows = lambda inv: (f"E:{inv.key}/ar", f"E:{inv.key}/rev")  # noqa: E731
        evidence = [tuple(sorted((orig.key, dup.key, a, b))) for a in posting_rows(orig) for b in posting_rows(dup)]
        issues.append(InternalIssue(
            key=f"issue:duplicate_invoice:{k}",
            type="duplicate_invoice",
            variant=variant,
            affected=tuple(sorted((orig.key, dup.key))),
            currency=orig.currency,
            tolerance_minor=DUPLICATE_TOLERANCE_MINOR,
            evidence_sets=evidence,
            amount_minor=orig.total_minor,
            params={"source_document": orig.key, "created_document": dup.key, "created_entry": f"E:{dup.key}",
                    "number": "shared" if variant == "same_number" else "new"},
        ))
    return issues


# ---------------------------------------------------------------------------- allocation
def _allocation_override(rng: Rng, books: Books, pay: Payment, variant: str) -> Tuple[str, Allocation, int]:
    target = rng.choice(list(pay.allocations or ()))
    applied = 0
    if variant == "partial":
        part = _nice_part(rng, target.amount_minor, pay.currency)
        if part is None:
            variant = "missing"
        else:
            applied = part
    books.allocation_overrides[pay.key] = {target.invoice_key: applied}
    return variant, target, applied


def _allocation_issue(key: str, variant: str, world: World, pay: Payment, target: Allocation, applied: int) -> InternalIssue:
    return InternalIssue(
        key=key,
        type="payment_allocation",
        variant=variant,
        affected=(pay.key,),
        currency=world.base_currency,
        tolerance_minor=ALLOCATION_TOLERANCE_MINOR,
        evidence_sets=[tuple(sorted((pay.key, f"E:{pay.key}/unapplied")))],
        amount_from_line=f"E:{pay.key}/unapplied",
        params={"payment": pay.key, "invoice": target.invoice_key, "remittance_amount_minor": target.amount_minor,
                "applied_amount_minor": applied,
                "unapplied_amount_minor_payment_currency": target.amount_minor - applied},
    )


def _allocation_candidates(world: World, books: Books, used: Set[str], foreign_only: bool = False) -> List[Payment]:
    return sorted(
        (p for p in books.payments
         if p.allocations is not None and _is_regular_receipt(world, p) and p.key not in used
         and p.key not in books.allocation_overrides
         and not any(a.invoice_key in used for a in p.allocations)
         and (not foreign_only or p.currency != world.base_currency)),
        key=lambda p: p.key,
    )


def inject_payment_allocation(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                              used: Set[str]) -> List[InternalIssue]:
    issues: List[InternalIssue] = []
    candidates = _allocation_candidates(world, books, used)
    for k, pay in enumerate(rng.sample(candidates, min(count, len(candidates)))):
        variant, target, applied = _allocation_override(rng, books, pay, rng.choice(variants))
        rate = receipt_policy_rate(world, pay)
        if money.convert(target.amount_minor - applied, pay.currency, world.base_currency, rate, world.rounding) <= 0:
            del books.allocation_overrides[pay.key]
            continue
        used.add(pay.key)
        used.update(a.invoice_key for a in pay.allocations or ())
        issues.append(_allocation_issue(f"issue:payment_allocation:{k}", variant, world, pay, target, applied))
    return issues


# ---------------------------------------------------------------------------- FX conversion
def _fx_issue(key: str, variant: str, world: World, doc_key: str, evidence: List[Tuple[str, ...]],
              booked: int, correct: int, params: Dict[str, Any]) -> InternalIssue:
    return InternalIssue(
        key=key,
        type="fx_conversion",
        variant=variant,
        affected=(doc_key,),
        currency=world.base_currency,
        tolerance_minor=FX_TOLERANCE_MINOR,
        evidence_sets=evidence,
        amount_minor=abs(booked - correct),
        params={**params, "booked_base_minor": booked, "correct_base_minor": correct},
    )


def _try_invoice_fx(rng: Rng, world: World, books: Books, inv: Invoice, variant: str) -> Optional[Tuple[Dict[str, Any], int, int, Dict[str, Any]]]:
    policy_rate = invoice_policy_rate(world, inv)
    day = world.local_date(inv.issued_at)
    correct = money.convert(inv.total_minor, inv.currency, world.base_currency, policy_rate, world.rounding)
    if variant == "invoice_wrong_date":
        alt = _other_date_rate(rng, world, inv.currency, day, policy_rate)
        if alt is None:
            return None
        override, params = {"rate": alt[0]}, {"rate_used": alt[0], "rate_date_used": alt[1]}
    elif variant == "invoice_wrong_rate":
        rate = _perturbed_rate(rng, policy_rate)
        override, params = {"rate": rate}, {"rate_used": rate}
    else:
        if money.EXPONENTS[inv.currency] == money.EXPONENTS[world.base_currency]:
            return None
        override, params = {"unscaled": True}, {"rate_used": policy_rate, "error": "minor units not rescaled"}
    probe = Books(invoices=[], payments=[], background=[], invoice_overrides={inv.key: override})
    booked = booked_invoice_conversion(world, probe, inv, inv.total_minor)
    if abs(booked - correct) < _fx_threshold(world, correct):
        return None
    return override, booked, correct, {**params, "policy_rate": policy_rate}


def _try_receipt_fx(rng: Rng, world: World, books: Books, pay: Payment, variant: str,
                    invoices: Dict[str, Invoice]) -> Optional[Tuple[str, int, int, Dict[str, Any]]]:
    policy_rate = receipt_policy_rate(world, pay)
    correct = money.convert(pay.amount_minor, pay.currency, world.base_currency, policy_rate, world.rounding)
    if variant == "receipt_wrong_date":
        rate, params = None, {}
        for a in pay.allocations or ():
            inv_rate = invoice_policy_rate(world, invoices[a.invoice_key])
            if inv_rate != policy_rate:
                rate, params = inv_rate, {"rate_used": inv_rate, "error": "receipt converted at the invoice-date rate"}
                break
        if rate is None:
            alt = _other_date_rate(rng, world, pay.currency, world.local_date(pay.received_at), policy_rate)
            if alt is None:
                return None
            rate, params = alt[0], {"rate_used": alt[0], "rate_date_used": alt[1]}
    else:
        rate = _perturbed_rate(rng, policy_rate)
        params = {"rate_used": rate}
    booked = money.convert(pay.amount_minor, pay.currency, world.base_currency, rate, world.rounding)
    if abs(booked - correct) < _fx_threshold(world, correct):
        return None
    return rate, booked, correct, {**params, "policy_rate": policy_rate}


def inject_fx_conversion(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                         used: Set[str]) -> List[InternalIssue]:
    invoices = {inv.key: inv for inv in books.invoices}
    inv_candidates = [inv for inv in sorted(books.invoices, key=lambda i: i.key)
                      if _is_regular_invoice(world, inv) and inv.key not in used and inv.currency != world.base_currency]
    pay_candidates = [p for p in sorted(books.payments, key=lambda p: p.key)
                      if _is_regular_receipt(world, p) and p.key not in used and p.currency != world.base_currency
                      and not any(a.invoice_key in used for a in p.allocations or ())]
    rng.shuffle(inv_candidates)
    rng.shuffle(pay_candidates)
    issues: List[InternalIssue] = []
    for k in range(count):
        variant = rng.choice(variants)
        order = [variant] + [v for v in variants if v != variant]
        issue = None
        for v in order:
            if v.startswith("invoice"):
                for inv in list(inv_candidates):
                    found = _try_invoice_fx(rng, world, books, inv, v)
                    if found is None:
                        continue
                    override, booked, correct, params = found
                    books.invoice_overrides.setdefault(inv.key, {}).update(override)
                    inv_candidates.remove(inv)
                    used.add(inv.key)
                    fx_key = world.fx_evidence_key(inv.currency, world.local_date(inv.issued_at))
                    issue = _fx_issue(f"issue:fx_conversion:{k}", v, world, inv.key,
                                      _invoice_posting_evidence(inv, fx_key), booked, correct,
                                      {"document": inv.key, "target": "invoice_posting", **params})
                    break
            else:
                for pay in list(pay_candidates):
                    found = _try_receipt_fx(rng, world, books, pay, v, invoices)
                    if found is None:
                        continue
                    rate, booked, correct, params = found
                    books.receipt_overrides.setdefault(pay.key, {})["rate"] = rate
                    pay_candidates.remove(pay)
                    used.add(pay.key)
                    fx_key = world.fx_evidence_key(pay.currency, world.local_date(pay.received_at))
                    issue = _fx_issue(f"issue:fx_conversion:{k}", v, world, pay.key,
                                      [tuple(sorted((pay.key, f"E:{pay.key}/cash", fx_key)))], booked, correct,
                                      {"document": pay.key, "target": "receipt_cash", **params})
                    break
            if issue is not None:
                break
        if issue is not None:
            issues.append(issue)
    return issues


# ---------------------------------------------------------------------------- period cut-off
def _cutoff_issue(key: str, variant: str, world: World, doc_key: str, line_tag: str,
                  evidence: List[Tuple[str, ...]], params: Dict[str, Any]) -> InternalIssue:
    return InternalIssue(
        key=key,
        type="period_cutoff",
        variant=variant,
        affected=(doc_key,),
        currency=world.base_currency,
        tolerance_minor=CUTOFF_TOLERANCE_MINOR,
        evidence_sets=evidence,
        amount_from_line=f"E:{doc_key}/{line_tag}",
        params=params,
    )


def _late_evening(rng: Rng, world: World) -> datetime:
    return _time_between(rng, world.period_end, world.tz, 18 * 60, 23 * 60 + 30)


def inject_period_cutoff(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                         alloc: TimeAllocator, used: Set[str]) -> List[InternalIssue]:
    offset = ZONE_OFFSETS_MINUTES[world.timezone]
    paid = _paid_invoice_keys(books)
    invs = sorted((i for i in books.invoices if i.kind == "invoice" and not i.key.startswith("inv#dup")),
                  key=lambda i: i.key)

    def free(inv: Invoice) -> bool:
        return inv.key not in used and inv.key not in paid and inv.key not in books.invoice_overrides

    def tz_candidate(inv: Invoice) -> bool:
        local = inv.issued_at.astimezone(world.tz)
        booked = _utc_wall_as_local(inv.issued_at, world)
        return world.in_period(local) != world.in_period(booked) and (world.in_period(booked) or world.in_window(booked))

    pools = {
        "early": [i for i in invs if world.in_window(i.issued_at)],
        "late": [i for i in invs if world.in_period(i.issued_at)
                 and world.local_date(i.issued_at) >= world.period_end - timedelta(days=9)],
        "tz_shift": [i for i in invs if offset != 0 and tz_candidate(i)],
        "receipt_early": [p for p in sorted(books.payments, key=lambda p: p.key)
                          if p.amount_minor > 0 and p.allocations is not None and world.in_window(p.received_at)],
    }
    for pool in pools.values():
        rng.shuffle(pool)

    issues: List[InternalIssue] = []
    for k in range(count):
        first = rng.choice(variants)
        for variant in [first] + [v for v in variants if v != first]:
            if variant == "receipt_early":
                pays = [p for p in pools[variant] if p.key not in used and p.key not in books.receipt_overrides
                        and not any(a.invoice_key in used for a in p.allocations or ())]
                if not pays:
                    continue
                pay = pays[0]
                booked = alloc.take(_late_evening(rng, world))
                books.receipt_overrides[pay.key] = {"at": booked}
                used.add(pay.key)
                issues.append(_cutoff_issue(f"issue:period_cutoff:{k}", variant, world, pay.key, "cash",
                                            [tuple(sorted((pay.key, f"E:{pay.key}/cash")))],
                                            {"document": pay.key, "document_time": pay.received_at.isoformat(),
                                             "booked_time": booked.isoformat(), "direction": "next period booked early"}))
                break
            cands = [i for i in pools[variant] if free(i)]
            if not cands:
                continue
            inv = cands[0]
            if variant == "early":
                booked, direction = _late_evening(rng, world), "next period booked early"
            elif variant == "late":
                day = world.period_end + timedelta(days=rng.randint(1, min(3, (world.window_end - world.period_end).days)))
                booked, direction = _business_time(rng, day, world.tz), "current period booked late"
            else:
                booked = _utc_wall_as_local(inv.issued_at, world)
                direction = "UTC wall-clock time stamped with the local offset"
            booked = alloc.take(booked)
            books.invoice_overrides.setdefault(inv.key, {})["at"] = booked
            used.add(inv.key)
            issues.append(_cutoff_issue(f"issue:period_cutoff:{k}", variant, world, inv.key, "ar",
                                        _invoice_posting_evidence(inv),
                                        {"document": inv.key, "document_time": inv.issued_at.isoformat(),
                                         "booked_time": booked.isoformat(), "direction": direction}))
            break
    return issues


# ---------------------------------------------------------------------------- interactions
def inject_fx_partial_at_cutoff(world: World, books: Books, rng: Rng, count: int,
                                alloc: TimeAllocator, used: Set[str]) -> List[InternalIssue]:
    """A partial foreign-currency receipt arrives just after period end; it is booked in the
    current period (cut-off) and, because of the same booking, at the invoice-date rate (FX)."""
    offset = ZONE_OFFSETS_MINUTES[world.timezone]
    allocated: Dict[str, int] = {}
    for p in books.payments:
        for a in p.allocations or ():
            allocated[a.invoice_key] = allocated.get(a.invoice_key, 0) + a.amount_minor
    candidates = [inv for inv in sorted(books.invoices, key=lambda i: i.key)
                  if _is_regular_invoice(world, inv) and inv.currency != world.base_currency and inv.key not in used
                  and inv.key not in books.invoice_overrides
                  and world.local_date(inv.issued_at) <= world.period_end - timedelta(days=3)
                  and inv.total_minor - allocated.get(inv.key, 0) > 0]
    rng.shuffle(candidates)
    next_day = world.period_end + timedelta(days=1)
    issues: List[InternalIssue] = []
    for k in range(count):
        for inv in list(candidates):
            open_amount = inv.total_minor - allocated.get(inv.key, 0)
            part = _nice_part(rng, open_amount, inv.currency)
            if part is None:
                continue
            inv_rate = invoice_policy_rate(world, inv)
            receipt_rate = world.rate(inv.currency, next_day)
            correct = money.convert(part, inv.currency, world.base_currency, receipt_rate, world.rounding)
            booked_cash = money.convert(part, inv.currency, world.base_currency, inv_rate, world.rounding)
            if abs(booked_cash - correct) < _fx_threshold(world, correct):
                continue
            if offset > 30:
                received = alloc.take(_time_between(rng, next_day, world.tz, 5, min(offset - 10, 4 * 60)))
                booked = alloc.take(_utc_wall_as_local(received, world))
                mechanism = "UTC wall-clock time stamped with the local offset"
            else:
                received = alloc.take(_time_between(rng, next_day, world.tz, 8 * 60, 11 * 60))
                booked = alloc.take(_late_evening(rng, world))
                mechanism = "receipt back-dated to the last day of the period"
            pay = Payment(key=f"pay#ix{k}", customer_key=inv.customer_key, received_at=received, currency=inv.currency,
                          amount_minor=part, allocations=(Allocation(inv.key, part),))
            books.payments.append(pay)
            books.receipt_overrides[pay.key] = {"at": booked, "rate": inv_rate}
            candidates.remove(inv)
            used.update((inv.key, pay.key))
            scenario = f"interaction:fx_partial_at_cutoff:{k}"
            common = {"scenario": "fx_partial_at_cutoff", "scenario_key": scenario, "document": pay.key,
                      "invoice": inv.key, "created_document": pay.key, "partial_amount_minor": part,
                      "open_amount_minor": open_amount}
            fx_key = world.fx_evidence_key(inv.currency, next_day)
            issues.append(_cutoff_issue(f"issue:{scenario}:cutoff", "receipt_early", world, pay.key, "cash",
                                        [tuple(sorted((pay.key, f"E:{pay.key}/cash")))],
                                        {**common, "document_time": received.isoformat(),
                                         "booked_time": booked.isoformat(), "mechanism": mechanism,
                                         "linked_issue": f"issue:{scenario}:fx"}))
            issues.append(_fx_issue(f"issue:{scenario}:fx", "receipt_wrong_date", world, pay.key,
                                    [tuple(sorted((pay.key, f"E:{pay.key}/cash", fx_key)))], booked_cash, correct,
                                    {**common, "target": "receipt_cash", "rate_used": inv_rate, "policy_rate": receipt_rate,
                                     "error": "converted at the invoice-date rate because of the same mis-dated booking",
                                     "linked_issue": f"issue:{scenario}:cutoff"}))
            break
    return issues


def inject_allocation_with_fx(world: World, books: Books, rng: Rng, count: int, variants: List[str],
                              used: Set[str]) -> List[InternalIssue]:
    """A foreign-currency receipt is only partly applied to its invoice and the whole
    receipt (including the unapplied remainder) is converted at a wrong rate."""
    invoices = {inv.key: inv for inv in books.invoices}
    candidates = _allocation_candidates(world, books, used, foreign_only=True)
    candidates = [p for p in candidates if p.key not in books.receipt_overrides]
    rng.shuffle(candidates)
    issues: List[InternalIssue] = []
    for k in range(count):
        for pay in list(candidates):
            fx_variant, found = "receipt_wrong_date", _try_receipt_fx(rng, world, books, pay, "receipt_wrong_date", invoices)
            if found is None:
                fx_variant, found = "receipt_wrong_rate", _try_receipt_fx(rng, world, books, pay, "receipt_wrong_rate", invoices)
            if found is None:
                continue
            rate, booked, correct, fx_params = found
            variant, target, applied = _allocation_override(rng, books, pay, rng.choice(variants))
            if money.convert(target.amount_minor - applied, pay.currency, world.base_currency, rate, world.rounding) <= 0:
                del books.allocation_overrides[pay.key]
                continue
            books.receipt_overrides[pay.key] = {"rate": rate}
            candidates.remove(pay)
            used.add(pay.key)
            used.update(a.invoice_key for a in pay.allocations or ())
            scenario = f"interaction:allocation_with_fx:{k}"
            alloc_issue = _allocation_issue(f"issue:{scenario}:allocation", variant, world, pay, target, applied)
            alloc_issue.params.update({"scenario": "allocation_with_fx", "scenario_key": scenario,
                                       "linked_issue": f"issue:{scenario}:fx",
                                       "note": "unapplied remainder converted at the same wrong rate"})
            fx_key = world.fx_evidence_key(pay.currency, world.local_date(pay.received_at))
            issues.append(alloc_issue)
            issues.append(_fx_issue(f"issue:{scenario}:fx", fx_variant, world, pay.key, [tuple(sorted((pay.key, f"E:{pay.key}/cash", fx_key)))],
                                    booked, correct,
                                    {**fx_params, "scenario": "allocation_with_fx", "scenario_key": scenario,
                                     "document": pay.key, "target": "receipt_cash",
                                     "linked_issue": f"issue:{scenario}:allocation"}))
            break
    return issues
