"""Journal entries as a pure function of the books (events + declared overrides).

Ledger is kept in the base (functional) currency. Documents keep their own currency;
conversion uses the policy rate (latest rate on or before the local document date).
Receipts credit AR at the invoice's carrying rate; the difference to cash at the
receipt-date rate is realized FX gain/loss.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from auditenv import money
from auditenv.generator.model import ACCOUNTS, Books, Invoice, LedgerLine, World


def _line(entry: str, tag: str, seq: int, at, account: str, debit: int, credit: int,
          entity: Optional[str], number: Optional[str], payment: Optional[str]) -> LedgerLine:
    if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
        raise AssertionError(f"invalid line amounts in {entry}/{tag}")
    return LedgerLine(f"{entry}/{tag}", entry, seq, at, account, debit, credit, entity, number, payment)


def invoice_base_amount(world: World, inv: Invoice) -> int:
    rate = world.rate(inv.currency, world.local_date(inv.issued_at))
    return money.convert(inv.total_minor, inv.currency, world.base_currency, rate, world.rounding)


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
        amount = invoice_base_amount(world, inv)
        carrying[inv.key] = amount
        entry = f"E:{inv.key}"
        lines.append(_line(entry, "ar", 0, inv.issued_at, ACCOUNTS["ar"], amount, 0, inv.customer_key, inv.number_key, None))
        lines.append(_line(entry, "rev", 1, inv.issued_at, ACCOUNTS["revenue"], 0, amount, inv.customer_key, inv.number_key, None))

    settled_cur: Dict[str, int] = defaultdict(int)
    settled_base: Dict[str, int] = defaultdict(int)
    for pay in sorted(books.payments, key=lambda p: (p.received_at, p.key)):
        entry = f"E:{pay.key}"
        rate = world.rate(pay.currency, world.local_date(pay.received_at))
        cash = money.convert(pay.amount_minor, pay.currency, base, rate, world.rounding)
        lines.append(_line(entry, "cash", 0, pay.received_at, ACCOUNTS["cash"], cash, 0, pay.customer_key, None, pay.key))
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
                    inv_rate = world.rate(inv.currency, world.local_date(inv.issued_at))
                    ar = money.convert(applied, inv.currency, base, inv_rate, world.rounding)
                settled_cur[key] += applied
                settled_base[key] += ar
                ar_total += ar
                lines.append(_line(entry, f"ar:{key}", seq, pay.received_at, ACCOUNTS["ar"], 0, ar,
                                   pay.customer_key, inv.number_key, pay.key))
                seq += 1
        unapplied_base = 0
        if unapplied_cur:
            unapplied_base = money.convert(unapplied_cur, pay.currency, base, rate, world.rounding)
        if unapplied_base:
            lines.append(_line(entry, "unapplied", seq, pay.received_at, ACCOUNTS["unapplied"], 0, unapplied_base,
                               pay.customer_key, None, pay.key))
            seq += 1
        fx = cash - ar_total - unapplied_base
        if fx > 0:
            lines.append(_line(entry, "fx", seq, pay.received_at, ACCOUNTS["fx"], 0, fx, pay.customer_key, None, pay.key))
        elif fx < 0:
            lines.append(_line(entry, "fx", seq, pay.received_at, ACCOUNTS["fx"], -fx, 0, pay.customer_key, None, pay.key))

    for bg in books.background:
        entry = f"E:{bg.key}"
        lines.append(_line(entry, "exp", 0, bg.at, bg.account, bg.amount_minor, 0, None, None, None))
        lines.append(_line(entry, "cash", 1, bg.at, ACCOUNTS["cash"], 0, bg.amount_minor, None, None, None))
    return lines
