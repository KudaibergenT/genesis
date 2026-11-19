import pytest

from auditenv import money
from auditenv.rng import Rng


def test_convert_rounds_once_to_target_minor_units():
    assert money.convert(12345, "EUR", "JPY", "161.237", "ROUND_HALF_EVEN") == 19905


def test_convert_respects_three_decimal_currencies():
    assert money.convert(1000, "USD", "KWD", "0.307", "ROUND_HALF_EVEN") == 3070


def test_rounding_modes_differ_on_ties():
    assert money.convert(5, "USD", "EUR", "0.5", "ROUND_HALF_EVEN") == 2
    assert money.convert(5, "USD", "EUR", "0.5", "ROUND_HALF_UP") == 3


def test_same_currency_is_identity_and_requires_unit_rate():
    assert money.convert(777, "KZT", "KZT", "1", "ROUND_HALF_EVEN") == 777
    with pytest.raises(ValueError):
        money.convert(777, "KZT", "KZT", "1.01", "ROUND_HALF_EVEN")


@pytest.mark.parametrize("bad", [True, 1.5, "10"])
def test_convert_rejects_non_integer_amounts(bad):
    with pytest.raises(TypeError):
        money.convert(bad, "USD", "EUR", "0.9", "ROUND_HALF_EVEN")


def test_format_minor():
    assert money.format_minor(19905, "JPY") == "19905 JPY"
    assert money.format_minor(3070, "KWD") == "3.070 KWD"
    assert money.format_minor(-5, "USD") == "-0.05 USD"


def test_rng_golden_values_are_stable():
    r = Rng("golden", 1)
    assert (r.u64(), r.randbelow(1000), r.randint(5, 9)) == (13049152111411452635, 341, 5)


def test_rng_streams_are_independent_and_deterministic():
    a, b = Rng("s", 1), Rng("s", 1)
    assert [a.u64() for _ in range(5)] == [b.u64() for _ in range(5)]
    assert Rng("s", 1).u64() != Rng("s", 2).u64()
    assert Rng("s", 1).child("x").u64() != Rng("s", 1).child("y").u64()


def test_rng_randbelow_is_in_range_and_covers_values():
    r = Rng("range")
    values = {r.randbelow(7) for _ in range(500)}
    assert values == set(range(7))
