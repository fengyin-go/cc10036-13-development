"""数据范围行过滤表达式的安全解析器。

只支持白名单语法，不使用 eval/exec，避免数据范围规则里的表达式被任意执行。
支持语法（SQL 风格，大小写不敏感）：

    字段 = '值' / 字段 != '值' / 字段 > 1 / 字段 >= 1 / 字段 < 1 / 字段 <= 1
    字段 IN ('a', 'b') / 字段 NOT IN ('a', 'b')
    条件 AND 条件 / 条件 OR 条件 / NOT 条件

字段名支持中文标识符，例如：城市 IN ('上海','杭州')。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ScopeFilterError(ValueError):
    """范围表达式无法解析时抛出，交付链路按未授权处理（fail-closed）。"""


# ---------------------------------------------------------------------------
# 词法分析
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str
    value: Any
    pos: int


def _tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            buf: list[str] = []
            while j < n and expr[j] != quote:
                buf.append(expr[j])
                j += 1
            if j >= n:
                raise ScopeFilterError(f"字符串字面量未闭合（位置 {i}）：{expr}")
            tokens.append(Token("STRING", "".join(buf), i))
            i = j + 1
            continue
        if ch.isdigit():
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            raw = expr[i:j]
            try:
                num = float(raw) if "." in raw else int(raw)
            except ValueError as exc:
                raise ScopeFilterError(f"非法数字字面量：{raw}") from exc
            tokens.append(Token("NUMBER", num, i))
            i = j
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", ch, i)); i += 1; continue
        if ch == ")":
            tokens.append(Token("RPAREN", ch, i)); i += 1; continue
        if ch == ",":
            tokens.append(Token("COMMA", ch, i)); i += 1; continue
        two = expr[i:i + 2]
        if two in (">=", "<=", "!=", "=="):
            tokens.append(Token("OP", two, i)); i += 2; continue
        if ch in "=<>":
            tokens.append(Token("OP", ch, i)); i += 1; continue
        if ch.isalpha() or "一" <= ch <= "鿿" or ch == "_":
            j = i
            while j < n and (expr[j].isalnum() or "一" <= expr[j] <= "鿿" or expr[j] == "_"):
                j += 1
            word = expr[i:j]
            upper = word.upper()
            if upper in ("AND", "OR", "NOT", "IN"):
                tokens.append(Token(upper, upper, i))
            else:
                tokens.append(Token("IDENT", word, i))
            i = j
            continue
        raise ScopeFilterError(f"无法识别的字符（位置 {i}）：{ch}")
    tokens.append(Token("EOF", None, n))
    return tokens


# ---------------------------------------------------------------------------
# 语法分析 → 可求值节点
# ---------------------------------------------------------------------------

class _Node:
    def evaluate(self, row: dict[str, Any]) -> bool:  # pragma: no cover - 抽象方法
        raise NotImplementedError


@dataclass
class _Literal(_Node):
    value: Any

    def evaluate(self, row: dict[str, Any]) -> Any:
        return self.value


@dataclass
class _Field(_Node):
    name: str

    def evaluate(self, row: dict[str, Any]) -> Any:
        return row.get(self.name)


@dataclass
class _Compare(_Node):
    left: _Node
    op: str
    right: _Node

    def evaluate(self, row: dict[str, Any]) -> bool:
        lv = self.left.evaluate(row)
        rv = self.right.evaluate(row)
        if lv is None:
            return False
        ln, rn = _as_number(lv), _as_number(rv)
        if ln is not None and rn is not None:
            lv, rv = ln, rn
        else:
            lv, rv = str(lv), str(rv)
        if self.op in ("=", "=="):
            return lv == rv
        if self.op == "!=":
            return lv != rv
        if self.op == ">":
            return lv > rv
        if self.op == ">=":
            return lv >= rv
        if self.op == "<":
            return lv < rv
        if self.op == "<=":
            return lv <= rv
        raise ScopeFilterError(f"不支持的比较运算符：{self.op}")


@dataclass
class _Membership(_Node):
    left: _Node
    options: list[_Node]
    negate: bool

    def evaluate(self, row: dict[str, Any]) -> bool:
        lv = self.left.evaluate(row)
        if lv is None:
            return False
        values = [node.evaluate(row) for node in self.options]
        hit = any(_loose_equal(lv, v) for v in values)
        return (not hit) if self.negate else hit


@dataclass
class _Logic(_Node):
    op: str
    children: list[_Node]

    def evaluate(self, row: dict[str, Any]) -> bool:
        if self.op == "AND":
            return all(c.evaluate(row) for c in self.children)
        return any(c.evaluate(row) for c in self.children)


@dataclass
class _Not(_Node):
    child: _Node

    def evaluate(self, row: dict[str, Any]) -> bool:
        return not self.child.evaluate(row)


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _take(self, kind: str | None = None) -> Token:
        token = self.tokens[self.pos]
        if kind is not None and token.kind != kind:
            raise ScopeFilterError(f"期望 {kind}，实际得到 {token.kind}（位置 {token.pos}）")
        self.pos += 1
        return token

    def parse(self) -> _Node:
        if self._peek().kind == "EOF":
            return _Literal(True)
        node = self._parse_or()
        if self._peek().kind != "EOF":
            raise ScopeFilterError(f"表达式尾部存在多余 token（位置 {self._peek().pos}）")
        return node

    def _parse_or(self) -> _Node:
        node = self._parse_and()
        children = [node]
        while self._peek().kind == "OR":
            self._take()
            children.append(self._parse_and())
        return children[0] if len(children) == 1 else _Logic("OR", children)

    def _parse_and(self) -> _Node:
        node = self._parse_factor()
        children = [node]
        while self._peek().kind == "AND":
            self._take()
            children.append(self._parse_factor())
        return children[0] if len(children) == 1 else _Logic("AND", children)

    def _parse_factor(self) -> _Node:
        if self._peek().kind == "NOT":
            self._take()
            return _Not(self._parse_factor())
        return self._parse_predicate()

    def _parse_predicate(self) -> _Node:
        left = self._parse_value()
        token = self._peek()
        if token.kind == "IN":
            self._take("IN")
            options = self._parse_option_list()
            return _Membership(left, options, negate=False)
        if token.kind == "NOT":
            self._take("NOT")
            self._take("IN")
            options = self._parse_option_list()
            return _Membership(left, options, negate=True)
        if token.kind == "OP":
            op = self._take("OP").value
            right = self._parse_value()
            return _Compare(left, op, right)
        raise ScopeFilterError(f"条件缺少运算符（位置 {token.pos}）")

    def _parse_option_list(self) -> list[_Node]:
        self._take("LPAREN")
        options: list[_Node] = []
        if self._peek().kind != "RPAREN":
            options.append(self._parse_value())
            while self._peek().kind == "COMMA":
                self._take("COMMA")
                options.append(self._parse_value())
        self._take("RPAREN")
        if not options:
            raise ScopeFilterError("IN 列表不能为空")
        return options

    def _parse_value(self) -> _Node:
        token = self._peek()
        if token.kind == "IDENT":
            self._take()
            return _Field(token.value)
        if token.kind in ("STRING", "NUMBER"):
            self._take()
            return _Literal(token.value)
        raise ScopeFilterError(f"非法的操作数（位置 {token.pos}）")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (ValueError, TypeError):
        return None


def _loose_equal(left: Any, right: Any) -> bool:
    ln, rn = _as_number(left), _as_number(right)
    if ln is not None and rn is not None:
        return ln == rn
    return str(left) == str(right)


def compile_filter(expression: str) -> _Node:
    """把表达式编译成可重复求值的谓词节点。"""
    return _Parser(_tokenize(expression or "")).parse()


def evaluate_filter(expression: str, row: dict[str, Any]) -> bool:
    """对单行数据求值范围表达式；空表达式放行所有行。"""
    return compile_filter(expression).evaluate(row)


def filter_rows(expression: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按范围表达式过滤数据行，解析失败直接抛错，由调用方按未授权处理。"""
    predicate = compile_filter(expression)
    return [row for row in rows if predicate.evaluate(row)]
