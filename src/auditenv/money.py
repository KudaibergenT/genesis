"""Exact money arithmetic: integer minor units, decimal-string FX rates."""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal

# ISO 4217 minor-unit exponents for the currencies the generator may use.
EXPONENTS = {
    "AED": 2,
    "BHD": 3,
    "CHF": 2,
    "CNY": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "KWD": 3,
    "KZT": 2,
    "USD": 2,
}

ROUNDING_MODES = {
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_HALF_UP": ROUND_HALF_UP,
}

_CTX = Context(prec=60)


def exponent(currency: str) -> int:
    return EXPONENTS[currency]


def convert(amount_minor: int, src: str, dst: str, rate: str, rounding: str) -> int:
    """Convert minor units of `src` into minor units of `dst`; `rate` = dst per 1 src."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise TypeError("amount_minor must be int")
    if src == dst:
        if Decimal(rate) != 1:
            raise ValueError("same-currency conversion requires rate 1")
        return amount_minor
    value = _CTX.multiply(Decimal(amount_minor), Decimal(rate))
    value = value.scaleb(EXPONENTS[dst] - EXPONENTS[src], _CTX)
    return int(value.quantize(Decimal(1), rounding=ROUNDING_MODES[rounding], context=_CTX))


def format_minor(amount_minor: int, currency: str) -> str:
    exp = EXPONENTS[currency]
    sign = "-" if amount_minor < 0 else ""
    digits = str(abs(amount_minor)).rjust(exp + 1, "0")
    if exp == 0:
        return f"{sign}{digits} {currency}"
    return f"{sign}{digits[:-exp]}.{digits[-exp:]} {currency}"
