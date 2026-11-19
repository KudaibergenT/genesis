"""Clean company history: world, documents-as-events, background activity."""

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
    base = rng.choice(company["base_currencies"])
    zone = rng.choice(company["timezones"])
    rounding = rng.choice(cfg["rounding_modes"])
    pool = [c for c in cfg["currencies"]["foreign_pool"] if c != base]
    n_foreign = min(resolve_range(cfg["currencies"]["foreign_count"], rng), len(pool))
    foreign = tuple(sorted(rng.sample(pool, n_foreign)))

    names_rng = rng.child("names")
    company_name = f"{names_rng.choice(NAME_A)} {names_rng.choice(NAME_B)} {names_rng.choice(NAME_SUFFIX)}"
    n_customers = resolve_range(cfg["size"]["customers"], rng)
    used_names = {company_name}
    customers: Dict[str, Customer] = {}
    for i in range(n_customers):
        while True:
            name = f"{names_rng.choice(NAME_A)} {names_rng.choice(NAME_B)} {names_rng.choice(NAME_SUFFIX)}"
            if name not in used_names:
                used_names.add(name)
                break
        currency = base
        if foreign and rng.chance(cfg["currencies"]["foreign_customer_per_mille"]):
            currency = rng.choice(foreign)
        customers[f"cust#{i}"] = Customer(key=f"cust#{i}", name=name, currency=currency)

    tz = tzinfo_for(zone)
    fx =_build_fx(rng.child("fx"), base, foreign, period_start - timedelta(days=10), period_end)
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
    )

    invoices = _build_invoices(rng.child("invoices"), world, cfg, alloc)
    payments = _build_payments(rng.child("payments"), world, cfg, invoices, alloc)
    background = _build_background(rng.child("background"), world, cfg, alloc)
    expenses = sum(b.amount_minor for b in background)
    world.opening_cash_minor = int(_sig_round(Decimal(expenses * 3 // 2 + 1), 3))

    summary = {
        "period": [period_start.isoformat(), period_end.isoformat()],
        "base_currency": base,
        "timezone": zone,
        "rounding": rounding,
        "foreign_currencies": list(foreign),
        "customers": n_customers,
        "invoices": len(invoices),
        "payments": len(payments),
        "background_entries": len(background),
    }
    return world, Books(invoices=invoices, payments=payments, background=background), alloc, summary


def _price(rng: Rng, currency: str) -> int:
    if rng.chance(150):
        usd = rng.randint(3000, 600000)
        return _to_minor(usd, currency, 4)
    return _to_minor(rng.choice(NICE_USD_CENTS), currency, rng.choice((2, 2, 3)))


def _build_invoices(rng: Rng, world: World, cfg: Dict[str, Any], alloc: TimeAllocator) -> List[Invoice]:
    target = resolve_range(cfg["size"]["invoices"], rng)
    customer_keys = sorted(world.customers)
    period_days = _days(world.period_start, world.period_end)
    tz = world.tz
    specs: List[Tuple[str, date, Tuple[Line, ...]]] = []

    if cfg["distractors"]["weekly_subscriptions"]:
        mondays = [d for d in period_days if d.weekday() == 0]
        for cust in rng.sample(customer_keys, min(2, len(customer_keys))):
            currency = world.customers[cust].currency
            item = rng.choice(WEEKLY_ITEMS)
            price = _price(rng, currency)
            for monday in mondays:
                iso = monday.isocalendar()
                line = Line(item, f"{iso[0]}-W{iso[1]:02d}", None, 1, price)
                specs.append((cust, monday, (line,)))

    month_tag = f"{world.period_start.year}-{world.period_start.month:02d}"
    for cust in rng.sample(customer_keys, max(1, len(customer_keys) // 3)):
        currency = world.customers[cust].currency
        line = Line(rng.choice(MONTHLY_ITEMS), month_tag, None, 1, _price(rng, currency))
        specs.append((cust, rng.choice(period_days[:10]), (line,)))

    order_numbers = rng.sample(range(10000, 99999), max(target, 1) * 2)
    order_iter = iter(order_numbers)
    while len(specs) < target:
        cust = rng.choice(customer_keys)
        currency = world.customers[cust].currency
        order_ref = f"PO-{next(order_iter)}"
        lines = []
        for _ in range(rng.weighted(((1, 50), (2, 30), (3, 15), (4, 5)))):
            qty = rng.weighted(((1, 60), (2, 15), (3, 10), (5, 8), (10, 7)))
            lines.append(Line(rng.choice(ONE_OFF_ITEMS), None, order_ref, qty, _price(rng, currency)))
        specs.append((cust, rng.choice(period_days), tuple(lines)))

    invoices = []
    for i, (cust, day, lines) in enumerate(specs):
        issued = alloc.take(_business_time(rng, day, tz))
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
        return day if day <= world.period_end else None

    behaviours = [("full", 560), ("unpaid", 220)]
    if d["split_payments"]:
        behaviours.append(("split", 130))
    if d["partial_payments"]:
        behaviours.append(("partial", 90))

    for inv in sorted(invoices, key=lambda x: (x.issued_at, x.key)):
        issue_day = world.local_date(inv.issued_at)
        total = inv.total_minor
        kind = rng.weighted(behaviours)
        if kind == "full":
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


def _build_background(rng: Rng, world: World, cfg: Dict[str, Any], alloc: TimeAllocator) -> List[Background]:
    n = resolve_range(cfg["size"]["background_entries"], rng)
    days = _days(world.period_start, world.period_end)
    out = []
    for i in range(n):
        account = rng.weighted(tuple((a, w) for a, w in zip(EXPENSE_ACCOUNTS, (3, 4, 8, 14, 30, 22, 6))))
        lo, hi = EXPENSE_USD_CENTS[account]
        amount = _to_minor(rng.randint(lo, hi), world.base_currency, rng.choice((3, 4)))
        at = alloc.take(_business_time(rng, rng.choice(days), world.tz))
        out.append(Background(key=f"bg#{i}", at=at, account=account, amount_minor=amount))
    return out
