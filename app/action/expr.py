"""actions.yaml의 식을 평가한다.

Python 식 문법의 일부만 허용한다. 허용 문법은 ontology/actions.yaml 주석을 따른다.
eval()은 쓰지 않는다. 먼저 ast를 검사해 허용하지 않은 문법을 거부하고
그 다음 ast를 직접 따라가며 평가한다.

객체를 찾고 속성과 링크를 따라가는 일은 평가기가 하지 않고 Resolver에게 맡긴다.
평가기는 객체가 어떻게 생겼는지 모른다.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

CONTEXT_NAMES = frozenset({"target", "actor", "input", "now"})
CONSTANTS: dict[str, Any] = {"null": None, "true": True, "false": False}
CODE_VALUE = re.compile(r"^[A-Z][A-Z0-9_]*$")
OBJECT_TYPE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
FUNCTION_ARITY = {"any": 1, "none": 1, "count": 1, "first": 1, "coalesce": None}
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


class ExprError(Exception):
    """허용하지 않는 문법이거나, 평가할 수 없는 식."""


class Resolver(Protocol):
    """평가기가 객체를 다룰 때 부르는 인터페이스. 조회 엔진이나 테스트용 저장소가 구현한다."""

    def has_type(self, type_name: str) -> bool:
        """객체 타입이 있는가."""

    def get(self, type_name: str, object_id: Any) -> Any | None:
        """Machine(17)처럼 객체 하나를 찾는다. 없으면 None."""

    def attr(self, obj: Any, name: str) -> Any:
        """저장 속성, 파생 속성, 링크(inverse 이름 포함). 없는 이름이면 ExprError."""


# ---------------------------------------------------------------------------
# 컴파일: 문법 검사
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Expr:
    source: str
    tree: ast.Expression

    def evaluate(self, resolver: Resolver, names: Mapping[str, Any]) -> Any:
        """names에는 target, actor, input, now를 넣는다."""
        try:
            result = _Evaluator(resolver).eval(self.tree.body, dict(names))
            if isinstance(result, Iterator):
                result = list(result)
        except ExprError as e:
            raise ExprError(f"{self.source!r}: {e}") from None
        except TypeError as e:
            raise ExprError(f"{self.source!r}: 평가할 수 없습니다 ({e})") from None
        return result


@lru_cache(maxsize=1024)
def compile_expr(source: str) -> Expr:
    """식을 파싱하고 허용 문법인지 검사한다. 문법이 틀리면 ExprError."""
    text = source.strip()
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"{source!r}: 식을 파싱할 수 없습니다 ({e.msg})") from None
    bound = {
        node.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name)
    }
    try:
        _check(tree.body, bound)
    except ExprError as e:
        raise ExprError(f"{source!r}: {e}") from None
    return Expr(text, tree)


def _check(node: ast.AST, bound: set[str]) -> None:
    match node:
        case ast.Constant(value=value):
            if type(value) not in (str, int, float):
                raise ExprError(f"값 {value!r}은 쓸 수 없습니다. null, true, false를 쓰세요")
        case ast.Name(id=name):
            _check_name(name, bound)
        case ast.Attribute(value=value, attr=attr):
            if attr.startswith("_"):
                raise ExprError(f"밑줄로 시작하는 속성 '{attr}'은 쓸 수 없습니다")
            _check(value, bound)
        case ast.BoolOp(values=values):
            for v in values:
                _check(v, bound)
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            _check(operand, bound)
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            if not all(isinstance(op, _COMPARE_OPS) for op in ops):
                raise ExprError("쓸 수 없는 비교 연산자입니다 (is, is not 대신 ==, != )")
            for v in (left, *comparators):
                _check(v, bound)
        case ast.IfExp(test=test, body=body, orelse=orelse):
            for v in (test, body, orelse):
                _check(v, bound)
        case ast.List(elts=elts):
            for v in elts:
                _check(v, bound)
        case (
            ast.ListComp(elt=elt, generators=generators)
            | ast.GeneratorExp(elt=elt, generators=generators)
        ):
            for gen in generators:
                _check_comprehension(gen, bound)
            _check(elt, bound)
        case ast.Call():
            _check_call(node, bound)
        case _:
            raise ExprError(f"쓸 수 없는 문법입니다: {type(node).__name__}")


def _check_name(name: str, bound: set[str]) -> None:
    if name in CONTEXT_NAMES or name in CONSTANTS or name in bound or CODE_VALUE.match(name):
        return
    if OBJECT_TYPE.match(name):
        raise ExprError(f"객체 타입 '{name}'은 {name}(id)처럼 불러서 씁니다")
    raise ExprError(f"모르는 이름 '{name}'")


def _check_comprehension(gen: ast.comprehension, bound: set[str]) -> None:
    if gen.is_async:
        raise ExprError("async for는 쓸 수 없습니다")
    target = gen.target
    if not isinstance(target, ast.Name):
        raise ExprError("for 뒤에는 이름 하나만 씁니다")
    if target.id in CONTEXT_NAMES or target.id in CONSTANTS or CODE_VALUE.match(target.id):
        raise ExprError(f"'{target.id}'은 for 변수 이름으로 쓸 수 없습니다")
    _check(gen.iter, bound)
    for cond in gen.ifs:
        _check(cond, bound)


def _check_call(node: ast.Call, bound: set[str]) -> None:
    if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
        raise ExprError("함수 인자는 위치 인자만 씁니다")
    func = node.func
    if isinstance(func, ast.Name) and func.id in FUNCTION_ARITY:
        arity = FUNCTION_ARITY[func.id]
        if arity is not None and len(node.args) != arity:
            raise ExprError(f"{func.id}()는 인자 {arity}개를 받습니다")
        if arity is None and not node.args:
            raise ExprError(f"{func.id}()에는 인자가 필요합니다")
    elif (
        isinstance(func, ast.Name) and OBJECT_TYPE.match(func.id) and not CODE_VALUE.match(func.id)
    ):
        if len(node.args) != 1:
            raise ExprError(f"{func.id}()는 id 하나를 받습니다")
    else:
        raise ExprError(f"부를 수 없는 함수입니다: {ast.unparse(func)}")
    for arg in node.args:
        _check(arg, bound)


# ---------------------------------------------------------------------------
# 평가
# ---------------------------------------------------------------------------


class _Evaluator:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def eval(self, node: ast.AST, env: dict[str, Any]) -> Any:
        match node:
            case ast.Constant(value=value):
                return value
            case ast.Name(id=name):
                return self._name(name, env)
            case ast.Attribute(value=value, attr=attr):
                return self._attr(self.eval(value, env), attr)
            case ast.BoolOp(op=ast.And(), values=values):
                result: Any = True
                for v in values:
                    result = self.eval(v, env)
                    if not result:
                        return result
                return result
            case ast.BoolOp(op=ast.Or(), values=values):
                result = False
                for v in values:
                    result = self.eval(v, env)
                    if result:
                        return result
                return result
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return not self.eval(operand, env)
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                lhs = self.eval(left, env)
                for op, comp in zip(ops, comparators, strict=True):
                    rhs = self.eval(comp, env)
                    if not _compare(op, lhs, rhs):
                        return False
                    lhs = rhs
                return True
            case ast.IfExp(test=test, body=body, orelse=orelse):
                return self.eval(body if self.eval(test, env) else orelse, env)
            case ast.List(elts=elts):
                return [self.eval(v, env) for v in elts]
            case ast.ListComp():
                return list(self._comprehension(node, env))
            case ast.GeneratorExp():
                return self._comprehension(node, env)
            case ast.Call():
                return self._call(node, env)
        raise ExprError(f"쓸 수 없는 문법입니다: {type(node).__name__}")  # compile_expr이 막는다

    def _name(self, name: str, env: dict[str, Any]) -> Any:
        if name in env:
            return env[name]
        if name in CONSTANTS:
            return CONSTANTS[name]
        if CODE_VALUE.match(name):
            return name
        raise ExprError(f"'{name}'의 값이 없습니다")

    def _attr(self, base: Any, attr: str) -> Any:
        if base is None:
            return None
        if isinstance(base, Mapping):
            return base.get(attr)
        return self.resolver.attr(base, attr)

    def _comprehension(self, node: ast.ListComp | ast.GeneratorExp, env: dict[str, Any]):
        generators = node.generators

        def walk(i: int, scope: dict[str, Any]) -> Iterator[Any]:
            if i == len(generators):
                yield self.eval(node.elt, scope)
                return
            gen = generators[i]
            for item in _iterable(self.eval(gen.iter, scope)):
                inner = {**scope, gen.target.id: item}
                if all(self.eval(cond, inner) for cond in gen.ifs):
                    yield from walk(i + 1, inner)

        return walk(0, env)

    def _call(self, node: ast.Call, env: dict[str, Any]) -> Any:
        name = node.func.id  # compile_expr이 이름 호출만 허용한다
        if name == "coalesce":
            for arg in node.args:  # 앞에서부터 하나씩 평가하고, null이 아닌 첫 값에서 멈춘다
                value = self.eval(arg, env)
                if value is not None:
                    return value
            return None
        if name in FUNCTION_ARITY:
            items = _iterable(self.eval(node.args[0], env))
            if name == "any":
                return any(items)
            if name == "none":
                return not any(items)
            if name == "count":
                return sum(1 for _ in items)
            return next(iter(items), None)  # first
        if not self.resolver.has_type(name):
            raise ExprError(f"모르는 객체 타입 '{name}'")
        object_id = self.eval(node.args[0], env)
        return None if object_id is None else self.resolver.get(name, object_id)


def _iterable(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, str | Mapping) or not isinstance(value, Iterable):
        raise ExprError(f"목록이 아닌 값은 돌 수 없습니다: {type(value).__name__}")
    return value


def _compare(op: ast.cmpop, lhs: Any, rhs: Any) -> bool:
    match op:
        case ast.Eq():
            return lhs == rhs
        case ast.NotEq():
            return lhs != rhs
        case ast.In():
            return rhs is not None and lhs in rhs
        case ast.NotIn():
            return rhs is None or lhs not in rhs
    if lhs is None or rhs is None:
        raise ExprError("null과는 크기를 비교할 수 없습니다")
    match op:
        case ast.Lt():
            return lhs < rhs
        case ast.LtE():
            return lhs <= rhs
        case ast.Gt():
            return lhs > rhs
        case ast.GtE():
            return lhs >= rhs
    raise ExprError(f"쓸 수 없는 비교 연산자입니다: {type(op).__name__}")


# ---------------------------------------------------------------------------
# 메시지 템플릿: "미해결 고장 {count(...)}건이 남아 있습니다"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    source: str
    parts: tuple[str | Expr, ...]

    def render(self, resolver: Resolver, names: Mapping[str, Any]) -> str:
        return "".join(
            part if isinstance(part, str) else _format(part.evaluate(resolver, names))
            for part in self.parts
        )


@lru_cache(maxsize=256)
def compile_template(source: str) -> Template:
    parts: list[str | Expr] = []
    pos = 0
    for match in PLACEHOLDER.finditer(source):
        parts.append(source[pos : match.start()])
        parts.append(compile_expr(match.group(1)))
        pos = match.end()
    parts.append(source[pos:])
    return Template(source, tuple(p for p in parts if p != ""))


def _format(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
