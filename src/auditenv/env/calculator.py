"""Arithmetic-only calculator: decimal literals, + - * /, unary sign, parentheses.

A hand-written recursive-descent parser over Decimal; no eval/ast, so no names,
attributes, calls, exponentiation or file access can ever be reached.
"""

from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import List

MAX_EXPRESSION_CHARS = 256
MAX_TOKENS = 128
MAX_DEPTH = 24
MAX_LITERAL_DIGITS = 30
MAX_ABS_RESULT = Decimal("1e30")

_CTX = Context(prec=40, Emax=60, Emin=-60, traps=[DivisionByZero, InvalidOperation, Overflow])


class CalculatorError(ValueError):
    pass


def _tokenize(expr: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in " \t":
            i += 1
        elif ch in "+-*/()":
            tokens.append(ch)
            i += 1
        elif ch.isascii() and (ch.isdigit() or ch == "."):
            j = i
            while j < len(expr) and expr[j].isascii() and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            literal = expr[i:j]
            digits = literal.replace(".", "")
            if literal.count(".") > 1 or not digits or len(digits) > MAX_LITERAL_DIGITS:
                raise CalculatorError("invalid number literal")
            tokens.append(literal)
            i = j
        else:
            raise CalculatorError("only digits, '.', '+', '-', '*', '/', parentheses and spaces are allowed")
        if len(tokens) > MAX_TOKENS:
            raise CalculatorError("expression too long")
    return tokens


class _Parser:
    def __init__(self, tokens: List[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.peek()
        if tok is None:
            raise CalculatorError("unexpected end of expression")
        self.pos += 1
        return tok

    def expr(self, depth: int) -> Decimal:
        value = self.term(depth)
        while self.peek() in ("+", "-"):
            op = self.take()
            rhs = self.term(depth)
            value = _CTX.add(value, rhs) if op == "+" else _CTX.subtract(value, rhs)
        return value

    def term(self, depth: int) -> Decimal:
        value = self.factor(depth)
        while self.peek() in ("*", "/"):
            op = self.take()
            rhs = self.factor(depth)
            value = _CTX.multiply(value, rhs) if op == "*" else _CTX.divide(value, rhs)
        return value

    def factor(self, depth: int) -> Decimal:
        if depth > MAX_DEPTH:
            raise CalculatorError("expression nested too deeply")
        tok = self.take()
        if tok in ("+", "-"):
            value = self.factor(depth + 1)
            return _CTX.minus(value) if tok == "-" else value
        if tok == "(":
            value = self.expr(depth + 1)
            if self.take() != ")":
                raise CalculatorError("missing closing parenthesis")
            return value
        if tok in ("*", "/", ")"):
            raise CalculatorError("unexpected operator")
        return Decimal(tok)


def evaluate(expression: object) -> str:
    if not isinstance(expression, str):
        raise CalculatorError("expression must be a string")
    if not expression.strip() or len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"expression must be 1..{MAX_EXPRESSION_CHARS} characters")
    parser = _Parser(_tokenize(expression))
    try:
        value = parser.expr(0)
    except DivisionByZero:
        raise CalculatorError("division by zero") from None
    except (InvalidOperation, Overflow):
        raise CalculatorError("arithmetic error") from None
    if parser.peek() is not None:
        raise CalculatorError("unexpected token after expression")
    if abs(value) > MAX_ABS_RESULT:
        raise CalculatorError("result out of range")
    if value == 0:
        return "0"
    text = format(value.normalize(_CTX), "f")
    return text
