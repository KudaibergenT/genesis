"""Journal entries as a pure function of the books (events + declared booking overrides).

Ledger is kept in the base (functional) currency. Documents keep their own currency;
conversion uses the policy rate (latest rate on or before the local document date).
Receipts credit AR at the invoice's carrying rate; the difference to cash at the
receipt-date rate is realized FX gain/loss. Credit notes and refunds carry negative
amounts and post to the opposite sides.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from auditenv import money
from auditenv.generator.model import ACCOUNTS, Books, Invoice, LedgerLine, Payment, World


def _line(entry: str, tag: str, seq: int, at, account: str, debit: int, credit: int,
          entity: Optional[str], number: Optional[str], payment: Optional[str]) -> LedgerLine:
    if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
        raise AssertionError(f"invalid line amounts in {entry}/{tag}")
    return LedgerLine(f"{entry}/{tag}", entry, seq, at, account, debit, credit, entity, number, payment)


def _sides(amount: int, natural_debit: bool) -> Tuple[int, int]:
    """Positive amounts go to the natural side, negative ones to the other side."""
    if (amount > 0) == natural_debit:
        return abs(amount), 0
    return 0, abs(amount)


def invoice_policy_rate(world: World, inv: Invoice) -> str:
    return world.rate(inv.currency, world.local_date(inv.issued_at))


def receipt_policy_rate(world: World, pay: Payment) -> str:
    return world.rate(pay.currency, world.local_date(pay.received_at))


def invoice_base_amount(world: World, inv: Invoice) -> int:
    return money.convert(inv.total_minor, inv.currency, world.base_currency, invoice_policy_rate(world, inv), world.rounding)


def booked_invoice_conversion(world: World, books: Books, inv: Invoice, amount: int) -> int:
    override = books.invoice_overrides.get(inv.key, {})
    rate = override.get("rate") or invoice_policy_rate(world, inv)
    if override.get("unscaled"):
        return money.convert_unscaled(amount, rate, world.rounding)
    return money.convert(amount, inv.currency, world.base_currency, rate, world.rounding)


def booked_receipt_rate(world: World, books: Books, pay: Payment) -> str:
    return books.receipt_overrides.get(pay.key, {}).get("rate") or receipt_policy_rate(world, pay)


def build_ledger(world: World, books: Books) -> List[LedgerLine]:
    base = world.base_currency
    lines: List[LedgerLine] = []

    entry = "E:open"
    lines.append(_line(entry, "cash", 0, world.opening_at, ACCOUNTS["cash"], world.opening_cash_minor, 0, None, None, None))
    lines.append(_line(entry, "equity", 1, world.opening_at, ACCOUNTS["equity"], 0, world.opening_cash_minor, None, None, None))

    invoices: Dict[str, Invoice] = {}
    carrying: Dict[str, int] = {}
    for inv in books.invoices:
        invoices[inv.key] = inv
        amount = booked_invoice_conversion(world, books, inv, inv.total_minor)
        carrying[inv.key] = amount
        at = books.invoice_overrides.get(inv.key, {}).get("at", inv.issued_at)
        counter = ACCOUNTS["revenue"] if amount > 0 else ACCOUNTS["returns"]
        entry = f"E:{inv.key}"
        lines.append(_line(entry, "ar", 0, at, ACCOUNTS["ar"], *_sides(amount, True), inv.customer_key, inv.number_key, None))
        lines.append(_line(entry, "rev", 1, at, counter, *_sides(amount, False), inv.customer_key, inv.number_key, None))

    settled_cur: Dict[str, int] = defaultdict(int)
    settled_base: Dict[str, int] = defaultdict(int)
    for pay in sorted(books.payments, key=lambda p: (p.received_at, p.key)):
        entry = f"E:{pay.key}"
        at = books.receipt_overrides.get(pay.key, {}).get("at", pay.received_at)
        rate = booked_receipt_rate(world, books, pay)
        cash = money.convert(pay.amount_minor, pay.currency, base, rate, world.rounding)
        lines.append(_line(entry, "cash", 0, at, ACCOUNTS["cash"], *_sides(cash, True), pay.customer_key, None, pay.key))
        seq = 1
        ar_total = 0
        unapplied_cur = pay.amount_minor
        if pay.allocations is not None:
            overrides = books.allocation_overrides.get(pay.key, {})
            for alloc in pay.allocations:
                inv = invoices[alloc.invoice_key]
                if inv.currency != pay.currency:
                    raise AssertionError("cross-currency allocation is not modelled in this version")
                applied = overrides.get(alloc.invoice_key, alloc.amount_minor)
                unapplied_cur -= applied
                if applied == 0:
                    continue
                key = alloc.invoice_key
                if settled_cur[key] + applied == inv.total_minor:
                    ar = carrying[key] - settled_base[key]
                else:
                    ar = booked_invoice_conversion(world, books, inv, applied)
                settled_cur[key] += applied
                settled_base[key] += ar
                ar_total += ar
                lines.append(_line(entry, f"ar:{key}", seq, at, ACCOUNTS["ar"], *_sides(ar, False),
                                   pay.customer_key, inv.number_key, pay.key))
                seq += 1
        unapplied_base = 0
        if unapplied_cur:
            unapplied_base = money.convert(unapplied_cur, pay.currency, base, rate, world.rounding)
        if unapplied_base:
            lines.append(_line(entry, "unapplied", seq, at, ACCOUNTS["unapplied"], *_sides(unapplied_base, False),
                               pay.customer_key, None, pay.key))
            seq += 1
        fx = cash - ar_total - unapplied_base
        if fx:
            lines.append(_line(entry, "fx", seq, at, ACCOUNTS["fx"], *_sides(fx, False), pay.customer_key, None, pay.key))

    for bg in books.background:
        entry = f"E:{bg.key}"
        lines.append(_line(entry, "exp", 0, bg.at, bg.account, bg.amount_minor, 0, None, None, None))
        lines.append(_line(entry, "cash", 1, bg.at, ACCOUNTS["cash"], 0, bg.amount_minor, None, None, None))
    return lines
