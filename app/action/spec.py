"""ontology/actions.yaml을 읽어 엔진이 쓰는 모양으로 바꾼다.

식은 읽을 때 모두 컴파일한다. 문법이 틀린 식이 있으면 서버가 뜰 때 바로 실패한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.action.expr import Expr, Template, compile_expr, compile_template

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "ontology" / "actions.yaml"
INPUT_TYPES = frozenset({"int", "string", "enum"})
STAGE_OF_SECTION = {"permission_criteria": "PERMISSION", "criteria": "PRECONDITION"}

# create의 values는 키마다 식이거나, evidence처럼 한 단계 더 들어간 묶음이다
Values = Mapping[str, "Expr | Values"]


class SpecError(Exception):
    """actions.yaml 모양이 엔진이 기대하는 것과 다르다."""


@dataclass(frozen=True)
class ReasonCode:
    code: str
    stage: str
    message: str


@dataclass(frozen=True)
class InputField:
    name: str
    type: str
    required: bool
    values: tuple[str, ...] = ()
    ref: str | None = None
    max_length: int | None = None


@dataclass(frozen=True)
class Criterion:
    description: str
    check: Expr
    reason: str
    when: Expr | None = None
    message: Template | None = None
    detail: Mapping[str, Expr] = field(default_factory=dict)


@dataclass(frozen=True)
class Effect:
    when: Expr | None = None
    create: str | None = None
    values: Values = field(default_factory=dict)
    set_object: Expr | None = None
    set_field: str | None = None
    set_value: Expr | None = None


@dataclass(frozen=True)
class Observe:
    objects: Expr
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    target: str
    target_key: str  # input에서 대상을 찾는 필드. target의 primary key 이름
    input: Mapping[str, InputField]
    allowed_roles: frozenset[str]
    permission_criteria: tuple[Criterion, ...]
    criteria: tuple[Criterion, ...]
    effects: tuple[Effect, ...]
    observe: tuple[Observe, ...]
    remaining: Mapping[str, Expr]


@dataclass(frozen=True)
class ActionsSpec:
    reason_codes: Mapping[str, ReasonCode]
    actions: Mapping[str, ActionSpec]


def load_actions(path: Path = DEFAULT_PATH) -> ActionsSpec:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return parse_actions(data)


def parse_actions(data: Mapping[str, Any]) -> ActionsSpec:
    reason_codes = {
        code: ReasonCode(code, body["stage"], body["message"])
        for code, body in (data.get("reason_codes") or {}).items()
    }
    actions = {
        name: _parse_action(name, body, reason_codes)
        for name, body in data.items()
        if name != "reason_codes"
    }
    return ActionsSpec(reason_codes, actions)


def _parse_action(
    name: str, body: Mapping[str, Any], reason_codes: Mapping[str, ReasonCode]
) -> ActionSpec:
    try:
        target = body["target"]
        target_key = target[0].lower() + target[1:] + "Id"
        inputs = {k: _parse_input(k, v) for k, v in body["input"].items()}
        if target_key not in inputs:
            raise SpecError(f"input에 대상 키 {target_key}가 없습니다")
        criteria = {
            section: tuple(_parse_criterion(c) for c in body.get(section) or [])
            for section in STAGE_OF_SECTION
        }
        for section, items in criteria.items():
            for c in items:
                code = reason_codes.get(c.reason)
                if code is None:
                    raise SpecError(f"reason_codes에 없는 코드 {c.reason}")
                if code.stage != STAGE_OF_SECTION[section]:
                    raise SpecError(f"{c.reason}는 {section}에 둘 수 없습니다 (stage {code.stage})")
        return ActionSpec(
            name=name,
            description=body["description"],
            target=target,
            target_key=target_key,
            input=inputs,
            allowed_roles=frozenset(body["allowed_roles"]),
            permission_criteria=criteria["permission_criteria"],
            criteria=criteria["criteria"],
            effects=tuple(_parse_effect(e) for e in body["effect"]),
            observe=tuple(
                Observe(compile_expr(o["objects"]), tuple(o["fields"]))
                for o in body.get("observe") or []
            ),
            remaining={k: compile_expr(v) for k, v in (body.get("remaining") or {}).items()},
        )
    except (KeyError, TypeError) as e:
        raise SpecError(f"{name}: 모양이 맞지 않습니다 ({e!r})") from None
    except SpecError as e:
        raise SpecError(f"{name}: {e}") from None


def _parse_input(name: str, body: Mapping[str, Any]) -> InputField:
    if body["type"] not in INPUT_TYPES:
        raise SpecError(f"input.{name}: 모르는 type {body['type']}")
    return InputField(
        name=name,
        type=body["type"],
        required=bool(body.get("required", False)),
        values=tuple(body.get("values") or ()),
        ref=body.get("ref"),
        max_length=body.get("max_length"),
    )


def _parse_criterion(body: Mapping[str, Any]) -> Criterion:
    return Criterion(
        description=body["description"],
        check=compile_expr(body["check"]),
        reason=body["reason"],
        when=compile_expr(body["when"]) if "when" in body else None,
        message=compile_template(body["message"]) if "message" in body else None,
        detail={k: compile_expr(v) for k, v in (body.get("detail") or {}).items()},
    )


def _parse_effect(body: Mapping[str, Any]) -> Effect:
    when = compile_expr(body["when"]) if "when" in body else None
    if "create" in body:
        return Effect(when=when, create=body["create"], values=_parse_values(body["values"]))
    set_ = body["set"]
    return Effect(
        when=when,
        set_object=compile_expr(set_["object"]),
        set_field=set_["field"],
        set_value=compile_expr(set_["value"]),
    )


def _parse_values(body: Mapping[str, Any]) -> Values:
    return {
        k: _parse_values(v) if isinstance(v, Mapping) else compile_expr(v) for k, v in body.items()
    }
