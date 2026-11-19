import pytest

from auditenv.env.calculator import CalculatorError, evaluate


@pytest.mark.parametrize("expr, expected", [
    ("1 + 2 * 3", "7"),
    ("(1+2)*3", "9"),
    ("10 / 4", "2.5"),
    ("-3 + 5", "2"),
    ("--2", "2"),
    ("12345 * 161.237 / 100", "19904.70765"),
    ("0.1 + 0.2", "0.3"),
    ("100 - 100", "0"),
    ("1.", "1"),
])
def test_arithmetic(expr, expected):
    assert evaluate(expr) == expected


@pytest.mark.parametrize("expr", [
    "",
    "   ",
    "a + 1",
    "__import__('os').system('id')",
    "open('/etc/passwd')",
    "2 ** 3",
    "1e5",
    "1.2.3",
    "1 / 0",
    "0 / 0",
    "(1 + 2",
    "1 + 2)",
    "1 +",
    "* 2",
    "9" * 31,
    "(" * 30 + "1" + ")" * 30,
    "1+" * 200 + "1",
    "١٢",
    "1 % 2",
    "[1]",
])
def test_rejects_non_arithmetic(expr):
    with pytest.raises(CalculatorError):
        evaluate(expr)


@pytest.mark.parametrize("value", [None, 5, ["1"], {"e": "1"}])
def test_rejects_non_string(value):
    with pytest.raises(CalculatorError):
        evaluate(value)


def test_result_range_is_bounded():
    with pytest.raises(CalculatorError):
        evaluate("999999999999999999999 * 999999999999999999999")
