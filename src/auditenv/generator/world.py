"""Clean company history: world, documents-as-events, background activity.

Besides the reporting period the history covers a review window of a few days after
period_end (next period). Window items are correct next-period activity; they exist so
cut-off can be tested from both sides of the boundary.
"""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Any, Dict, List, Optional, Tuple

from auditenv import money
from auditenv.generator.model import (
    EXPENSE_ACCOUNTS,
    Allocation,
    Background,
    Books,
    Customer,
    Invoice,
    Line,
    Payment,
    TimeAllocator,
    World,
)
from auditenv.rng import Rng, resolve_range
from auditenv.timeutil import days_in_month, tzinfo_for

# Rough units per 1 USD, used only to give synthetic prices and rates plausible scales.
APPROX_PER_USD = {
    "AED": "3.6725",
    "BHD": "0.376",
    "CHF": "0.88",
    "CNY": "7.2",
    "EUR": "0.92",
    "GBP": "0.79",
    "JPY": "150",
    "KWD": "0.307",
    "KZT": "505",
    "USD": "1",
}

NAME_A = ("Blue Ridge", "Granite", "Juniper", "Meridian", "Lattice", "Kestrel", "Saffron", "Tundra",
          "Verdant", "Quartz", "Harbor", "Cobalt", "Amber", "Silverline", "Orchard", "Basalt")
NAME_B = ("Analytics", "Logistics", "Foods", "Systems", "Textiles", "Robotics", "Media", "Agro",
          "Labs", "Energy", "Retail", "Build", "Optics", "Freight", "Clinics", "Studios")
NAME_SUFFIX = ("LLP", "Ltd", "GmbH", "LLC", "JSC", "BV")

ONE_OFF_ITEMS = (
    "Consulting services", "Implementation milestone", "Data migration", "Training workshop",
    "Hardware: edge gateway", "Hardware: sensor kit", "Custom integration", "Audit support package",
    "Logistics handling", "Translation services", "Equipment calibration", "Design sprint",
)
MONTHLY_ITEMS = ("Cloud hosting - Standard plan", "Support retainer", "Software licence - Team tier",
                 "Managed backup")
WEEKLY_ITEMS = ("Field service - weekly visit", "Equipment rental - weekly", "Cleaning services - weekly")

NICE_USD_CENTS = (4900, 9900, 12000, 15000, 18000, 25000, 32000, 39000, 48000, 60000, 75000,
                  95000, 120000, 150000, 180000, 240000, 310000, 420000, 560000, 780000)
EXPENSE_USD_CENTS = {
    "6100 Rent": (180000, 900000),
    "6200 Payroll": (250000, 2400000),
    "6300 Utilities": (8000, 90000),
    "6400 Software Subscriptions": (2000, 60000),
    "6500 Bank Charges": (300, 9000),
    "6600 Travel": (6000, 240000),
    "6700 Professional Fees": (40000, 600000),
}

_CTX = Context(prec=40)


def _sig_round(value: Decimal, digits: int) -> Decimal:
    if value == 0:
        return value
    quantum = Decimal(1).scaleb(value.adjusted() - digits + 1)
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN, context=_CTX)


def _to_minor(usd_cents: int, currency: str, sig_digits: int) -> int:
    """Scale a USD-cent magnitude into a plausible price in `currency` minor units."""
    value = _CTX.multiply(Decimal(usd_cents), Decimal(APPROX_PER_USD[currency]))
    value = value.scaleb(money.EXPONENTS[currency] - 2, _CTX)
    value = _sig_round(value, sig_digits)
    return max(int(value), 1)


def _business_time(rng: Rng, day: date, tz) -> datetime:
    return datetime.combine(day, time(rng.randint(9, 17), rng.randint(0, 59), rng.randint(0, 59)), tz)


def _time_between(rng: Rng, day: date, tz, start_minute: int, end_minute: int) -> datetime:
    minute = rng.randint(start_minute, end_minute)
    return datetime.combine(day, time(minute // 60, minute % 60, rng.randint(0, 59)), tz)


def _days(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _build_fx(rng: Rng, base: str, foreign: Tuple[str, ...], start: date, end: date) -> Dict[str, List[Tuple[date, str]]]:
    table: Dict[str, List[Tuple[date, str]]] = {}
    for cur in foreign:
        level = _CTX.divide(Decimal(APPROX_PER_USD[base]), Decimal(APPROX_PER_USD[cur]))
        level = _CTX.multiply(level, Decimal(1000 + rng.randint(-60, 60)) / Decimal(1000))
        series: List[Tuple[date, str]] = []
        for d in _days(start, end):
            level = _CTX.multiply(level, Decimal(250000 + rng.randint(-1000, 1000)) / Decimal(250000))
            if d.weekday() >= 5:
                continue
            series.append((d, format(_sig_round(level, 6), "f")))
        table[cur] = series
    return table


def build_world(seed: int, cfg: Dict[str, Any], world_hash: str) -> Tuple[World, Books, TimeAllocator, Dict[str, Any]]:
    rng = Rng("world", seed, world_hash)
    company = cfg["company"]
    year = company["year"]
    month = resolve_range(company["months"], rng)
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month(year, month))
    window_end = period_end + timedelta(days=company["review_window_days"])
    base = rng.choice(company["base_currencies"])
    zone = rng.choice(company["timezones"])
    payment_zone = rng.choice(company["payment_timestamp_zones"])
    rounding = rng.choice(cfg["rounding_modes"])
    pool = [c for c in cfg["currencies"]["foreign_pool"] if c != base]
    n_foreign = min(resolve_range(cfg["currencies"]["foreign_count"], rng), len(pool))
    foreign = tuple(sorted(rng.sample(pool, n_foreign)))

    names_rng = rng.child("names")
    company_name = f"{names_rng.choice(NAME_A)} {names_rng.choice(NAME_B)} {names_rng.choice(NAME_SUFFIX)}"
    n_customers = resolve_range(cfg["size"]["customers"], rng)
    used_names = {company_name}
    currencies: List[str] = []
    for _ in range(n_customers):
        currency = base
        if foreign and rng.chance(cfg["currencies"]["foreign_customer_per_mille"]):
            currency = rng.choice(foreign)
        currencies.append(currency)
    # Multi-currency companies always have some foreign-currency customers.
    if foreign and cfg["currencies"]["foreign_customer_per_mille"] > 0:
        for i in range(min(2, n_customers - 1)):
            if currencies[i] == base:
                currencies[i] = foreign[i % len(foreign)]
    customers: Dict[str, Customer] = {}
    for i, currency in enumerate(currencies):
        while True:
            name = f"{names_rng.choice(NAME_A)} {names_rng.choice(NAME_B)} {names_rng.choice(NAME_SUFFIX)}"
            if name not in used_names:
                used_names.add(name)
                break
        customers[f"cust#{i}"] = Customer(key=f"cust#{i}", name=name, currency=currency)

    tz = tzinfo_for(zone)
    fx = _build_fx(rng.child("fx"), base, foreign, period_start - timedelta(days=10), window_end)
    opening_at = datetime.combine(period_start, time(0, 0, 0), tz)
    alloc = TimeAllocator()
    alloc.take(opening_at)

    world = World(
        seed=seed,
        company_name=company_name,
        base_currency=base,
        timezone=zone,
        period_start=period_start,
        period_end=period_end,
        rounding=rounding,
        foreign_currencies=foreign,
        customers=customers,
        fx=fx,
        opening_at=opening_at,
        opening_cash_minor=0,
        window_end=window_end,
        payment_zone=payment_zone,
    )

    invoices = _build_invoices(rng.child("invoices"), world, cfg, alloc)
    payments = _build_payments(rng.child("payments"), world, cfg, invoices, alloc)
    if cfg["distractors"]["refunds"]:
        credit_notes, refunds = _build_refunds(rng.child("refunds"), world, invoices, payments, alloc)
        invoices += credit_notes
        payments += refunds
    background = _build_background(rng.child("background"), world, cfg, alloc)
    expenses = sum(b.amount_minor for b in background)
    world.opening_cash_minor = int(_sig_round(Decimal(expenses * 3 // 2 + 1), 3))

    summary = {
        "period": [period_start.isoformat(), period_end.isoformat()],
        "review_window_end": window_end.isoformat(),
        "base_currency": base,
        "timezone": zone,
        "payment_timestamp_zone": payment_zone,
        "rounding": rounding,
        "foreign_currencies": list(foreign),
        "customers": n_customers,
        "invoices": len(invoices),
        "credit_notes": sum(1 for i in invoices if i.kind == "credit_note"),
        "payments": len(payments),
        "background_entries": len(background),
    }
    return world, Books(invoices=invoices, payments=payments, background=background), alloc, summary


def _price(rng: Rng, currency: str) -> int:
    if rng.chance(150):
        usd = rng.randint(3000, 600000)
        return _to_minor(usd, currency, 4)
    return _to_minor(rng.choice(NICE_USD_CENTS), currency, rng.choice((2, 2, 3)))


def _one_off_lines(rng: Rng, currency: str, order_ref: str) -> Tuple[Line, ...]:
    lines = []
    for _ in range(rng.weighted(((1, 50), (2, 30), (3, 15), (4, 5)))):
        qty = rng.weighted(((1, 60), (2, 15), (3, 10), (5, 8), (10, 7)))
        lines.append(Line(rng.choice(ONE_OFF_ITEMS), None, order_ref, qty, _price(rng, currency)))
    return tuple(lines)


def _build_invoices(rng: Rng, world: World, cfg: Dict[str, Any], alloc: TimeAllocator) -> List[Invoice]:
    target = resolve_range(cfg["size"]["invoices"], rng)
    customer_keys = sorted(world.customers)
    period_days = _days(world.period_start, world.period_end)
    window_days = _days(world.period_end + timedelta(days=1), world.window_end)
    tz = world.tz
    specs: List[Tuple[str, datetime, Tuple[Line, ...]]] = []

    if cfg["distractors"]["weekly_subscriptions"]:
        mondays = [d for d in period_days if d.weekday() == 0]
        for cust in rng.sample(customer_keys, min(2, len(customer_keys))):
            currency = world.customers[cust].currency
            item = rng.choice(WEEKLY_ITEMS)
            price = _price(rng, currency)
            for monday in mondays:
                iso = monday.isocalendar()
                line = Line(item, f"{iso[0]}-W{iso[1]:02d}", None, 1, price)
                specs.append((cust, _business_time(rng, monday, tz), (line,)))

    month_tag = f"{world.period_start.year}-{world.period_start.month:02d}"
    for cust in rng.sample(customer_keys, max(1, len(customer_keys) // 3)):
        currency = world.customers[cust].currency
        line = Line(rng.choice(MONTHLY_ITEMS), month_tag, None, 1, _price(rng, currency))
        specs.append((cust, _business_time(rng, rng.choice(period_days[:10]), tz), (line,)))

    order_numbers = iter(rng.sample(range(10000, 99999), max(target, 1) * 2 + 20))

    def one_off(when: datetime) -> None:
        cust = rng.choice(customer_keys)
        specs.append((cust, when, _one_off_lines(rng, world.customers[cust].currency, f"PO-{next(order_numbers)}")))

    while len(specs) < target:
        one_off(_business_time(rng, rng.choice(period_days), tz))
    foreign_customers = [c for c in customer_keys if world.customers[c].currency != world.base_currency]
    early_days = period_days[: max(1, len(period_days) - 6)]
    early_foreign = sum(1 for cust, when, _ in specs
                        if cust in foreign_customers and world.local_date(when) in early_days)
    for _ in range(max(0, 6 - early_foreign) if foreign_customers else 0):
        cust = rng.choice(foreign_customers)
        when = _business_time(rng, rng.choice(early_days), tz)
        specs.append((cust, when, _one_off_lines(rng, world.customers[cust].currency, f"PO-{next(order_numbers)}")))
    if cfg["distractors"]["boundary_documents"]:
        # Late on the last day (current period) and just after local midnight (next period):
        # these dates differ between local time and UTC, so periods must be judged locally.
        # The first one is always in a foreign currency when the company has one.
        foreign_customers_list = [c for c in customer_keys if world.customers[c].currency != world.base_currency]
        for i in range(rng.randint(1, 2)):
            when = _time_between(rng, world.period_end, tz, 19 * 60, 23 * 60 + 40)
            if i == 0 and foreign_customers_list:
                cust = rng.choice(foreign_customers_list)
                specs.append((cust, when, _one_off_lines(rng, world.customers[cust].currency,
                                                         f"PO-{next(order_numbers)}")))
            else:
                one_off(when)
        for _ in range(rng.randint(1, 2)):
            one_off(_time_between(rng, world.period_end + timedelta(days=1), tz, 5, 4 * 60 + 30))
    for _ in range(rng.randint(2, 4)):
        one_off(_business_time(rng, rng.choice(window_days), tz))

    invoices = []
    for i, (cust, when, lines) in enumerate(specs):
        issued = alloc.take(when)
        terms = rng.choice((7, 14, 30, 30, 45))
        invoices.append(
            Invoice(
                key=f"inv#{i}",
                number_key=f"num#{i}",
                customer_key=cust,
                issued_at=issued,
                due_at=issued + timedelta(days=terms),
                currency=world.customers[cust].currency,
                lines=lines,
            )
        )
    return invoices


def _nice_part(rng: Rng, total: int, currency: str) -> Optional[int]:
    """A plausible partial amount strictly between 0 and total."""
    pct = rng.randint(25, 75)
    unit = 10 ** money.EXPONENTS[currency]
    part = total * pct // 100 // unit * unit
    if 0 < part < total:
        return part
    return None


def _build_payments(rng: Rng, world: World, cfg: Dict[str, Any], invoices: List[Invoice], alloc: TimeAllocator) -> List[Payment]:
    tz = world.tz
    d = cfg["distractors"]
    raw: List[Tuple[str, date, str, List[Allocation]]] = []  # customer, day, currency, allocations

    def pay_day(after: date, lo: int, hi: int) -> Optional[date]:
        day = after + timedelta(days=rng.randint(lo, hi))
        return day if day <= world.window_end else None

    behaviours = [("full", 560), ("unpaid", 220)]
    if d["split_payments"]:
        behaviours.append(("split", 130))
    if d["partial_payments"]:
        behaviours.append(("partial", 90))

    # A multi-currency company always has some foreign-currency receivables still open and
    # some settled within the period.
    early_foreign = [inv.key for inv in sorted(invoices, key=lambda x: (x.issued_at, x.key))
                     if inv.currency != world.base_currency and world.in_period(inv.issued_at)
                     and world.local_date(inv.issued_at) <= world.period_end - timedelta(days=6)]
    rng.shuffle(early_foreign)
    forced_kind = {key: "unpaid" for key in early_foreign[:2]}
    forced_kind.update({key: "full_in_period" for key in early_foreign[2:4]})
    # Every company also shows a correctly split payment and an early settled one-off sale
    # (the source of a refund), so clean look-alikes are always present.
    early_one_off = [inv.key for inv in sorted(invoices, key=lambda x: (x.issued_at, x.key))
                     if inv.key not in forced_kind and world.in_period(inv.issued_at)
                     and any(line.order_ref for line in inv.lines)
                     and world.local_date(inv.issued_at) <= world.period_end - timedelta(days=14)]
    rng.shuffle(early_one_off)
    if d["split_payments"] and early_one_off:
        forced_kind[early_one_off.pop()] = "split_in_period"
    if d["refunds"] and early_one_off:
        forced_kind[early_one_off.pop()] = "full_in_period"

    for inv in sorted(invoices, key=lambda x: (x.issued_at, x.key)):
        if not world.in_period(inv.issued_at):
            continue
        issue_day = world.local_date(inv.issued_at)
        total = inv.total_minor
        kind = forced_kind.get(inv.key) or rng.weighted(behaviours)
        if kind == "full_in_period":
            day = min(issue_day + timedelta(days=rng.randint(1, 5)), world.period_end)
            raw.append((inv.customer_key, day, inv.currency, [Allocation(inv.key, total)]))
        elif kind == "split_in_period":
            part = _nice_part(rng, total, inv.currency) or total // 2
            day = issue_day + timedelta(days=rng.randint(1, 4))
            day2 = min(day + timedelta(days=rng.randint(1, 5)), world.period_end)
            raw.append((inv.customer_key, day, inv.currency, [Allocation(inv.key, part)]))
            raw.append((inv.customer_key, day2, inv.currency, [Allocation(inv.key, total - part)]))
        elif kind == "full":
            day = pay_day(issue_day, 1, 20)
            if day:
                raw.append((inv.customer_key, day, inv.currency, [Allocation(inv.key, total)]))
        elif kind in ("split", "partial"):
            part = _nice_part(rng, total, inv.currency)
            day = pay_day(issue_day, 1, 12)
            if part is None or day is None:
                continue
            raw.append((inv.customer_key, day, inv.currency, [Allocation(inv.key, part)]))
            if kind == "split":
                day2 = pay_day(day, 1, 10)
                if day2:
                    raw.append((inv.customer_key, day2, inv.currency, [Allocation(inv.key, total - part)]))

    if d["bulk_payments"]:
        merged: List[Tuple[str, date, str, List[Allocation]]] = []
        used = [False] * len(raw)
        for i, (cust, day, cur, allocs) in enumerate(raw):
            if used[i]:
                continue
            used[i] = True
            group = list(allocs)
            last = day
            if rng.chance(350):
                for j in range(i + 1, len(raw)):
                    c2, d2, cur2, a2 = raw[j]
                    if not used[j] and c2 == cust and cur2 == cur and abs((d2 - day).days) <= 4:
                        if {a.invoice_key for a in a2} & {a.invoice_key for a in group}:
                            continue
                        used[j] = True
                        group.extend(a2)
                        last = max(last, d2)
                        if len(group) >= 3:
                            break
            merged.append((cust, last, cur, group))
        raw = merged

    payments: List[Payment] = []
    for i, (cust, day, cur, allocs) in enumerate(raw):
        received = alloc.take(_business_time(rng, day, tz))
        payments.append(
            Payment(
                key=f"pay#{i}",
                customer_key=cust,
                received_at=received,
                currency=cur,
                amount_minor=sum(a.amount_minor for a in allocs),
                allocations=tuple(allocs),
            )
        )

    if d["unidentified_payments"]:
        for j in range(rng.randint(1, 3)):
            cust = rng.choice(sorted(world.customers))
            cur = world.customers[cust].currency
            day = rng.choice(_days(world.period_start, world.period_end))
            payments.append(
                Payment(
                    key=f"pay#u{j}",
                    customer_key=cust,
                    received_at=alloc.take(_business_time(rng, day, tz)),
                    currency=cur,
                    amount_minor=_price(rng, cur),
                    allocations=None,
                )
            )
    return payments


def _build_refunds(rng: Rng, world: World, invoices: List[Invoice], payments: List[Payment],
                   alloc: TimeAllocator) -> Tuple[List[Invoice], List[Payment]]:
    """Returns: a credit note against a fully paid invoice, refunded to the customer in cash."""
    paid: Dict[str, int] = {}
    last_paid: Dict[str, date] = {}
    for pay in payments:
        for a in pay.allocations or ():
            paid[a.invoice_key] = paid.get(a.invoice_key, 0) + a.amount_minor
            day = world.local_date(pay.received_at)
            last_paid[a.invoice_key] = max(last_paid.get(a.invoice_key, day), day)
    cutoff = world.period_end - timedelta(days=4)
    candidates = sorted(
        (inv for inv in invoices
         if paid.get(inv.key) == inv.total_minor and last_paid[inv.key] <= cutoff
         and any(line.order_ref for line in inv.lines)),
        key=lambda inv: inv.key,
    )
    credit_notes: List[Invoice] = []
    refunds: List[Payment] = []
    for j, orig in enumerate(rng.sample(candidates, min(len(candidates), rng.randint(1, 2)))):
        line = rng.choice([ln for ln in orig.lines if ln.order_ref])
        returned = rng.randint(1, line.quantity)
        credit_line = Line(f"Credit note: returned {line.description}", line.service_period, line.order_ref,
                           -returned, line.unit_price_minor)
        cn_day = min(last_paid[orig.key] + timedelta(days=rng.randint(1, 3)), world.period_end)
        cn_at = alloc.take(_business_time(rng, cn_day, world.tz))
        cn = Invoice(
            key=f"inv#cn{j}",
            number_key=f"num#cn{j}",
            customer_key=orig.customer_key,
            issued_at=cn_at,
            due_at=cn_at + timedelta(days=14),
            currency=orig.currency,
            lines=(credit_line,),
            kind="credit_note",
        )
        credit_notes.append(cn)
        refund_day = min(cn_day + timedelta(days=rng.randint(1, 5)), world.window_end)
        refunds.append(
            Payment(
                key=f"pay#r{j}",
                customer_key=orig.customer_key,
                received_at=alloc.take(_business_time(rng, refund_day, world.tz)),
                currency=orig.currency,
                amount_minor=cn.total_minor,
                allocations=(Allocation(cn.key, cn.total_minor),),
            )
        )
    return credit_notes, refunds


def _build_background(rng: Rng, world: World, cfg: Dict[str, Any], alloc: TimeAllocator) -> List[Background]:
    n = resolve_range(cfg["size"]["background_entries"], rng)
    days = _days(world.period_start, world.window_end)
    out = []
    for i in range(n):
        account = rng.weighted(tuple((a, w) for a, w in zip(EXPENSE_ACCOUNTS, (3, 4, 8, 14, 30, 22, 6))))
        lo, hi = EXPENSE_USD_CENTS[account]
        amount = _to_minor(rng.randint(lo, hi), world.base_currency, rng.choice((3, 4)))
        at = alloc.take(_business_time(rng, rng.choice(days), world.tz))
        out.append(Background(key=f"bg#{i}", at=at, account=account, amount_minor=amount))
    return out
