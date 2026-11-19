"""Internal (hidden) company model. Internal keys never leave the generator unrendered."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from auditenv import timeutil

ACCOUNTS = {
    "cash": "1000 Cash at Bank",
    "ar": "1200 Accounts Receivable",
    "unapplied": "2400 Unapplied Customer Receipts",
    "equity": "3000 Opening Equity",
    "revenue": "4000 Revenue",
    "fx": "7900 Realized FX Gain/Loss",
}

EXPENSE_ACCOUNTS = (
    "6100 Rent",
    "6200 Payroll",
    "6300 Utilities",
    "6400 Software Subscriptions",
    "6500 Bank Charges",
    "6600 Travel",
    "6700 Professional Fees",
)


@dataclass(frozen=True)
class Customer:
    key: str
    name: str
    currency: str


@dataclass(frozen=True)
class Line:
    description: str
    service_period: Optional[str]
    order_ref: Optional[str]
    quantity: int
    unit_price_minor: int

    @property
    def amount_minor(self) -> int:
        return self.quantity * self.unit_price_minor


@dataclass(frozen=True)
class Invoice:
    key: str
    number_key: str
    customer_key: str
    issued_at: datetime
    due_at: datetime
    currency: str
    lines: Tuple[Line, ...]

    @property
    def total_minor(self) -> int:
        return sum(line.amount_minor for line in self.lines)


@dataclass(frozen=True)
class Allocation:
    invoice_key: str
    amount_minor: int


@dataclass(frozen=True)
class Payment:
    key: str
    customer_key: str
    received_at: datetime
    currency: str
    amount_minor: int
    allocations: Optional[Tuple[Allocation, ...]]


@dataclass(frozen=True)
class Background:
    key: str
    at: datetime
    account: str
    amount_minor: int


@dataclass(frozen=True)
class LedgerLine:
    key: str
    entry_key: str
    seq: int
    at: datetime
    account: str
    debit_minor: int
    credit_minor: int
    entity_key: Optional[str]
    number_key: Optional[str]
    payment_key: Optional[str]


@dataclass
class World:
    seed: int
    company_name: str
    base_currency: str
    timezone: str
    period_start: date
    period_end: date
    rounding: str
    foreign_currencies: Tuple[str, ...]
    customers: Dict[str, Customer]
    fx: Dict[str, List[Tuple[date, str]]]
    opening_at: datetime
    opening_cash_minor: int

    @property
    def tz(self):
        return timeutil.tzinfo_for(self.timezone)

    def local_date(self, dt: datetime) -> date:
        return timeutil.local_date(dt, self.timezone)

    def in_period(self, dt: datetime) -> bool:
        return self.period_start <= self.local_date(dt) <= self.period_end

    def rate(self, currency: str, on: date) -> str:
        """Policy: rate of the latest published date on or before `on`."""
        if currency == self.base_currency:
            return "1"
        best = None
        for d, rate in self.fx[currency]:
            if d <= on:
                best = rate
            else:
                break
        if best is None:
            raise LookupError(f"no FX rate for {currency} on or before {on}")
        return best


@dataclass
class Books:
    invoices: List[Invoice]
    payments: List[Payment]
    background: List[Background]
    allocation_overrides: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def copy(self) -> "Books":
        return Books(
            invoices=list(self.invoices),
            payments=list(self.payments),
            background=list(self.background),
            allocation_overrides={k: dict(v) for k, v in self.allocation_overrides.items()},
        )


class TimeAllocator:
    """Gives every journal entry a unique timestamp, so rows group into entries by time."""

    def __init__(self) -> None:
        self._used: Set[datetime] = set()

    def take(self, dt: datetime) -> datetime:
        while dt in self._used:
            dt += timedelta(seconds=37)
        self._used.add(dt)
        return dt
