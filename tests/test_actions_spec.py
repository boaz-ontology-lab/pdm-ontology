"""ontology/actions.yaml이 엔진이 읽을 수 있는 모양인지 검사한다.

표기 규칙은 check_conventions가 보고, 여기서는 구조와 식 문법을 본다.
식 문법은 app.action.expr의 평가기로 검사한다. 값이 맞게 나오는지는 액션 엔진 테스트에서 본다.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from check_conventions import check_actions

from app.action.expr import ExprError, compile_expr, compile_template

ROOT = Path(__file__).resolve().parents[1]
ACTIONS_PATH = ROOT / "ontology" / "actions.yaml"
CONVENTIONS_PATH = ROOT / "docs" / "conventions.md"

ROLES = {"FIELD_WORKER", "MECHANIC", "MANAGER", "VENDOR"}
STAGES = {"PERMISSION", "PRECONDITION"}
INPUT_TYPES = {"int", "string", "enum"}
CRITERION_KEYS = {"description", "when", "check", "reason", "message", "detail"}
EFFECT_KEYS = {"create", "values", "set", "when"}
# 엔진이 직접 만드는 코드라 criteria에 나오지 않아도 된다
ENGINE_CODES = {"ROLE_NOT_ALLOWED"}
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
CODE_VALUE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    with ACTIONS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _actions(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: body for name, body in spec.items() if name != "reason_codes"}


def _criteria(action: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for section in ("permission_criteria", "criteria"):
        for criterion in action.get(section) or []:
            yield section, criterion


def _expressions(action: dict[str, Any]) -> Iterator[str]:
    """액션 안에서 식으로 평가되는 문자열을 모두 꺼낸다."""
    for _, criterion in _criteria(action):
        for key in ("when", "check"):
            if key in criterion:
                yield criterion[key]
        yield from PLACEHOLDER.findall(criterion.get("message", ""))
        yield from _leaf_strings(criterion.get("detail") or {})
    for effect in action.get("effect") or []:
        if "when" in effect:
            yield effect["when"]
        yield from _leaf_strings(effect.get("values") or {})
        if "set" in effect:
            yield effect["set"]["object"]
            yield effect["set"]["value"]
    for item in action.get("observe") or []:
        yield item["objects"]
    yield from _leaf_strings(action.get("remaining") or {})


def _leaf_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaf_strings(v)
    else:
        yield value


def _code_values() -> set[str]:
    """docs/conventions.md 2절 표의 코드 값. 표기 규칙 문서를 하나뿐인 기준으로 쓴다."""
    text = CONVENTIONS_PATH.read_text(encoding="utf-8")
    section = text.split("## 2. 코드 값", 1)[1].split("\n## ", 1)[0]
    values: set[str] = set()
    for line in section.splitlines():
        cells = line.split("|")
        if len(cells) >= 4:
            values.update(v for v in re.findall(r"`([^`]+)`", cells[2]) if CODE_VALUE.match(v))
    return values


def _parsed(expressions: Iterator[str]) -> Iterator[ast.AST]:
    for expr in expressions:
        if isinstance(expr, str):
            yield from ast.walk(ast.parse(expr.strip(), mode="eval"))


def test_conventions(spec):
    assert check_actions(spec) == []


def test_reason_codes(spec):
    for code, body in spec["reason_codes"].items():
        assert body["stage"] in STAGES, code
        assert body["message"], code


def test_action_shape(spec):
    for name, action in _actions(spec).items():
        for key in ("description", "target", "input", "allowed_roles", "effect"):
            assert key in action, f"{name}: {key}가 없습니다"
        assert set(action["allowed_roles"]) <= ROLES, name


def test_target_key_in_input(spec):
    """대상은 input에서 target의 primary key(camelCase + Id)로 찾는다."""
    for name, action in _actions(spec).items():
        target = action["target"]
        key = target[0].lower() + target[1:] + "Id"
        assert key in action["input"], f"{name}: input에 {key}가 없습니다"
        assert action["input"][key].get("required") is True, name


def test_input_fields(spec):
    for name, action in _actions(spec).items():
        for field, field_spec in action["input"].items():
            where = f"{name}.input.{field}"
            assert field_spec["type"] in INPUT_TYPES, where
            assert isinstance(field_spec.get("required"), bool), where
            if field_spec["type"] == "enum":
                assert field_spec.get("values"), where


def test_criteria_reasons_match_stage(spec):
    codes = spec["reason_codes"]
    expected_stage = {"permission_criteria": "PERMISSION", "criteria": "PRECONDITION"}
    for name, action in _actions(spec).items():
        for section, criterion in _criteria(action):
            where = f"{name}.{section}: {criterion.get('description')}"
            assert set(criterion) <= CRITERION_KEYS, where
            assert criterion["description"] and criterion["check"], where
            assert criterion["reason"] in codes, where
            assert codes[criterion["reason"]]["stage"] == expected_stage[section], where


def test_every_reason_code_is_used(spec):
    used = {c["reason"] for a in _actions(spec).values() for _, c in _criteria(a)}
    unused = set(spec["reason_codes"]) - used - ENGINE_CODES
    assert not unused, f"쓰지 않는 거부 코드: {sorted(unused)}"


def test_effects_shape(spec):
    for name, action in _actions(spec).items():
        for effect in action["effect"]:
            assert set(effect) <= EFFECT_KEYS, name
            assert ("create" in effect) != ("set" in effect), f"{name}: create와 set 중 하나만"
            if "create" in effect:
                assert effect.get("values"), name
            else:
                assert set(effect["set"]) == {"object", "field", "value"}, name


def test_expressions_compile(spec):
    """식은 모두 평가기가 허용하는 문법이어야 한다. 값이 맞는지는 액션 엔진 테스트에서 본다."""
    for name, action in _actions(spec).items():
        for expr in _expressions(action):
            assert isinstance(expr, str), f"{name}: 식은 문자열이어야 합니다: {expr!r}"
            try:
                compile_expr(expr)
            except ExprError as e:
                pytest.fail(f"{name}: {e}")
        for _, criterion in _criteria(action):
            if "message" in criterion:
                compile_template(criterion["message"])


def test_code_values_are_known(spec):
    """식에 쓴 대문자 값이 표기 규칙의 코드 값인지 본다.

    평가기는 대문자 이름을 문자열 값으로 다루므로 RUNING 같은 오타도 문법상 통과하고,
    비교가 항상 거짓이 되어 사전조건이 조용히 틀린다.
    """
    known = _code_values()
    assert known, "docs/conventions.md에서 코드 값을 찾지 못했습니다"
    for name, action in _actions(spec).items():
        for node in _parsed(_expressions(action)):
            if isinstance(node, ast.Name) and CODE_VALUE.match(node.id):
                assert node.id in known, (
                    f"{name}: 코드 값 '{node.id}'이 docs/conventions.md 2절에 없습니다"
                )


def test_input_refs_are_declared(spec):
    """식의 input.x가 그 액션의 input에 정의되어 있는지 본다. 없으면 평가기가 null로 넘긴다."""
    for name, action in _actions(spec).items():
        declared = set(action.get("input") or {})
        for node in _parsed(_expressions(action)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "input"
            ):
                assert node.attr in declared, f"{name}: input.{node.attr}이 input에 없습니다"
