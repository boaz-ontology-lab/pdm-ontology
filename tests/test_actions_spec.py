"""ontology/actions.yaml이 엔진이 읽을 수 있는 모양인지 검사한다.

표기 규칙은 check_conventions가 보고, 여기서는 구조와 식 문법을 본다.
식이 실제로 맞게 평가되는지는 액션 엔진 테스트에서 본다.
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

ACTIONS_PATH = Path(__file__).resolve().parents[1] / "ontology" / "actions.yaml"

ROLES = {"FIELD_WORKER", "MECHANIC", "MANAGER", "VENDOR"}
STAGES = {"PERMISSION", "PRECONDITION"}
INPUT_TYPES = {"int", "string", "enum"}
CRITERION_KEYS = {"description", "when", "check", "reason", "message", "detail"}
EFFECT_KEYS = {"create", "values", "set", "when"}
# 엔진이 직접 만드는 코드라 criteria에 나오지 않아도 된다
ENGINE_CODES = {"ROLE_NOT_ALLOWED"}
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


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


def test_expressions_parse(spec):
    """식은 모두 Python 식으로 파싱되어야 한다. 허용 문법인지는 엔진이 다시 본다."""
    for name, action in _actions(spec).items():
        for expr in _expressions(action):
            assert isinstance(expr, str), f"{name}: 식은 문자열이어야 합니다: {expr!r}"
            try:
                ast.parse(expr.strip(), mode="eval")
            except SyntaxError as e:
                pytest.fail(f"{name}: 식을 파싱할 수 없습니다: {expr!r} ({e.msg})")
