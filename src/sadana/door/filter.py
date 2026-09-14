"""The console's filter grammar — a recursive-descent parser into an AST, and
`evaluate()` against a rendered row.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirement 8.
Fields `a` or `a.b`; operators `= != < <= > >= :` (`:` is string-only
"contains"); `AND OR NOT` with precedence `NOT > AND > OR`; parentheses;
double-quoted strings with `\\"` escapes; numbers; ISO-8601 dates;
`true`/`false`.

Pure: no field-allowlist check lives here (`grammar.py` owns "is this field
filterable for this noun", since that answer is per-noun, not a property of
the grammar itself) and no row storage is touched — `evaluate()` takes a
plain `Mapping` and answers a `bool`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_OPS = ("!=", "<=", ">=", "=", "<", ">", ":")
_KEYWORDS = {"AND", "OR", "NOT"}


class _SyntaxError(Exception):
    pass


# ── AST ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DateValue:
    """A bare ISO-8601 literal, holding its parsed epoch seconds so
    `evaluate()` can compare it against a row's own ISO-8601 string field
    numerically rather than lexicographically — two strings at different
    precision (`"2026-01-01"` vs a row's full millisecond `Z` timestamp) do
    not sort the way their instants do. A dataclass wrapper rather than a
    bare `float`, so `_compare()`'s `isinstance(literal, DateValue)` can
    still tell a date literal apart from an ordinary number."""

    epoch: float


@dataclass(frozen=True)
class Compare:
    field: str
    op: str
    value: str | float | bool | DateValue


@dataclass(frozen=True)
class BoolOp:
    kind: str  # "AND" | "OR" | "NOT"
    left: Node
    right: Node | None  # None for NOT


Node = Compare | BoolOp


# ── tokenizer ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Token:
    kind: str  # "FIELD" "OP" "STRING" "NUMBER" "DATE" "BOOL" "KEYWORD" "LPAREN" "RPAREN"
    text: str
    value: object = None


def _parse_iso8601(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(_Token("LPAREN", c))
            i += 1
            continue
        if c == ")":
            tokens.append(_Token("RPAREN", c))
            i += 1
            continue
        if c == '"':
            j = i + 1
            out = []
            while j < n and expr[j] != '"':
                if expr[j] == "\\" and j + 1 < n and expr[j + 1] in ('"', "\\"):
                    out.append(expr[j + 1])
                    j += 2
                else:
                    out.append(expr[j])
                    j += 1
            if j >= n:
                raise _SyntaxError("unterminated string literal")
            tokens.append(_Token("STRING", expr[i : j + 1], "".join(out)))
            i = j + 1
            continue
        matched = False
        for op in _OPS:
            if expr.startswith(op, i):
                tokens.append(_Token("OP", op))
                i += len(op)
                matched = True
                break
        if matched:
            continue
        m = _DATE_RE.match(expr, i)
        if m:
            date_text = m.group(0)
            epoch = _parse_iso8601(date_text)
            if epoch is not None:
                tokens.append(_Token("DATE", date_text, DateValue(epoch)))
                i = m.end()
                continue
        m = _NUMBER_RE.match(expr, i)
        if m:
            text = m.group(0)
            tokens.append(_Token("NUMBER", text, float(text) if "." in text else int(text)))
            i = m.end()
            continue
        m = _FIELD_RE.match(expr, i)
        if m:
            text = m.group(0)
            if text in _KEYWORDS:
                tokens.append(_Token("KEYWORD", text))
            elif text == "true":
                tokens.append(_Token("BOOL", text, True))
            elif text == "false":
                tokens.append(_Token("BOOL", text, False))
            else:
                tokens.append(_Token("FIELD", text))
            i = m.end()
            continue
        raise _SyntaxError(f"unexpected character {c!r} at position {i}")
    return tokens


# ── recursive-descent parser: NOT > AND > OR ────────────────────────────
class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> _Token:
        tok = self._peek()
        if tok is None:
            raise _SyntaxError("unexpected end of filter expression")
        self._pos += 1
        return tok

    def parse(self) -> Node:
        node = self._or()
        if self._peek() is not None:
            raise _SyntaxError(f"unexpected trailing token {self._peek().text!r}")  # type: ignore[union-attr]
        return node

    def _or(self) -> Node:
        left = self._and()
        while (tok := self._peek()) is not None and tok.kind == "KEYWORD" and tok.text == "OR":
            self._advance()
            right = self._and()
            left = BoolOp("OR", left, right)
        return left

    def _and(self) -> Node:
        left = self._not()
        while (tok := self._peek()) is not None and tok.kind == "KEYWORD" and tok.text == "AND":
            self._advance()
            right = self._not()
            left = BoolOp("AND", left, right)
        return left

    def _not(self) -> Node:
        tok = self._peek()
        if tok is not None and tok.kind == "KEYWORD" and tok.text == "NOT":
            self._advance()
            return BoolOp("NOT", self._not(), None)
        return self._atom()

    def _atom(self) -> Node:
        tok = self._peek()
        if tok is not None and tok.kind == "LPAREN":
            self._advance()
            node = self._or()
            closing = self._advance()
            if closing.kind != "RPAREN":
                raise _SyntaxError("missing closing parenthesis")
            return node
        return self._compare()

    def _compare(self) -> Node:
        field_tok = self._advance()
        if field_tok.kind != "FIELD":
            raise _SyntaxError(f"expected a field name, got {field_tok.text!r}")
        op_tok = self._advance()
        if op_tok.kind != "OP":
            raise _SyntaxError(f"expected an operator after {field_tok.text!r}, got {op_tok.text!r}")
        value_tok = self._advance()
        if value_tok.kind not in ("STRING", "NUMBER", "DATE", "BOOL"):
            raise _SyntaxError(f"expected a value after {op_tok.text!r}, got {value_tok.text!r}")
        if op_tok.text == ":" and value_tok.kind != "STRING":
            raise _SyntaxError("':' (contains) is only defined for string values")
        return Compare(field_tok.text, op_tok.text, value_tok.value)  # type: ignore[arg-type]


def parse(expr: str) -> Node:
    """Raises `_SyntaxError`. Callers in this package catch it and turn it
    into a `problems.Problem`; kept private so nothing outside `door/`
    depends on this module's own exception shape."""
    tokens = _tokenize(expr)
    if not tokens:
        raise _SyntaxError("empty filter expression")
    return _Parser(tokens).parse()


def fields(node: Node) -> frozenset[str]:
    """Every field name a parsed filter references — what `grammar.py`
    checks against a noun's declared `filterable` tuple."""
    if isinstance(node, Compare):
        return frozenset({node.field})
    left = fields(node.left)
    return left | (fields(node.right) if node.right is not None else frozenset())


def _resolve(row: Mapping[str, object], field: str) -> object:
    head, _, tail = field.partition(".")
    value = row.get(head)
    if not tail:
        return value
    if isinstance(value, Mapping):
        return value.get(tail)
    return None


def _compare(op: str, field_value: object, literal: str | float | bool | DateValue) -> bool:
    if isinstance(literal, DateValue):
        parsed = _parse_iso8601(field_value) if isinstance(field_value, str) else None
        if parsed is None:
            return op == "!="
        field_value = parsed
        literal_value: object = literal.epoch
    else:
        literal_value = literal

    if op == ":":
        return isinstance(field_value, str) and isinstance(literal_value, str) and literal_value in field_value
    try:
        if op == "=":
            return bool(field_value == literal_value)
        if op == "!=":
            return bool(field_value != literal_value)
        if op == "<":
            return bool(field_value < literal_value)  # type: ignore[operator]
        if op == "<=":
            return bool(field_value <= literal_value)  # type: ignore[operator]
        if op == ">":
            return bool(field_value > literal_value)  # type: ignore[operator]
        if op == ">=":
            return bool(field_value >= literal_value)  # type: ignore[operator]
    except TypeError:
        return False
    raise AssertionError(f"unreachable operator {op!r}")


def evaluate(node: Node, row: Mapping[str, object]) -> bool:
    if isinstance(node, Compare):
        return _compare(node.op, _resolve(row, node.field), node.value)
    if node.kind == "NOT":
        return not evaluate(node.left, row)
    if node.kind == "AND":
        return evaluate(node.left, row) and evaluate(node.right, row)  # type: ignore[arg-type]
    return evaluate(node.left, row) or evaluate(node.right, row)  # type: ignore[arg-type]
