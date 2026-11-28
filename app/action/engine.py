"""액션 엔진. actions.yaml 정의대로 액션 하나를 검사하고 실행한다.

검사 순서: 사용자 확인 → 액션 찾기 → 입력 검증 → 대상 찾기 → 권한 → 사전조건 → 실행
결과와 상관없이 호출마다 감사 로그를 한 행 남기고, 그 번호를 응답의 actionId로 돌려준다.
응답 모양은 docs/api.md 3장을 따른다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.action.spec import ActionSpec, ActionsSpec, Criterion, InputField, Values
from app.action.store import AuditRecord, Store, StoreConflict
from app.messages import COMMON_MESSAGES

ERROR, DENIED, ALLOWED = "ERROR", "DENIED", "ALLOWED"


@dataclass(frozen=True)
class ActionResponse:
    status: int
    body: dict[str, Any]


@dataclass
class _Call:
    """호출 하나에서 감사 로그에 남길 것을 모은다."""

    action: str
    user_id: str | None
    body: Any
    at: datetime
    actor: Any = None
    actor_role: str | None = None
    target_type: str | None = None
    target_id: Any = None
    names: dict[str, Any] = field(default_factory=dict)


class _Rejected(Exception):
    def __init__(
        self, status: int, result: str, stage: str, violations: list[dict], message: str
    ) -> None:
        super().__init__(violations[0]["code"])
        self.status = status
        self.body = {
            "result": result,
            "stage": stage,
            "reasonCode": violations[0]["code"],
            "message": message,
            "violations": violations,
        }


class ActionEngine:
    def __init__(self, spec: ActionsSpec, store: Store, clock: Callable[[], datetime]) -> None:
        self.spec = spec
        self.store = store
        self.clock = clock

    def execute(self, action_name: str, user_id: str | None, body: Any) -> ActionResponse:
        call = _Call(action=action_name, user_id=user_id, body=body, at=self.clock())
        try:
            status, response = self._run(call)
            result, stage, reason_code = ALLOWED, None, None
        except _Rejected as r:
            status, response = r.status, r.body
            result, stage, reason_code = r.body["result"], r.body["stage"], r.body["reasonCode"]

        action_id = self.store.append_audit(
            AuditRecord(
                action=action_name,
                result=result,
                stage=stage,
                reason_code=reason_code,
                actor_id=user_id,
                actor_role=call.actor_role,
                target_type=call.target_type,
                target_id=call.target_id,
                input=body if isinstance(body, Mapping) else {"body": body},
                response=response,
                at=call.at,
            )
        )
        if result == ALLOWED:
            return ActionResponse(status, {"actionId": action_id, **response})
        return ActionResponse(status, {**response, "actionId": action_id})

    # ------------------------------------------------------------------
    # 검사 단계
    # ------------------------------------------------------------------

    def _run(self, call: _Call) -> tuple[int, dict[str, Any]]:
        user = self._authenticate(call)
        action = self.spec.actions.get(call.action)
        if action is None:
            raise _common(404, ERROR, "INPUT", "NOT_FOUND", {"action": call.action})
        inputs = self._validate_input(action, call.body)

        call.target_type = action.target
        call.target_id = inputs[action.target_key]
        target = self.store.get(action.target, call.target_id)
        if target is None:
            detail = {"type": action.target, "id": call.target_id}
            raise _common(404, ERROR, "INPUT", "NOT_FOUND", detail)

        call.names = {"target": target, "actor": user, "input": inputs, "now": call.at}
        self._check_permission(action, call)
        self._check(action.criteria, call, status=409, stage="PRECONDITION")
        return self._execute(action, call, target)

    def _authenticate(self, call: _Call) -> Any:
        """X-User-Id 헤더 값으로 사용자를 찾는다. 역할은 헤더로 받지 않고 저장소에서 읽는다."""
        user = self.store.get("User", call.user_id) if call.user_id else None
        if user is None:
            raise _common(401, ERROR, "AUTH", "UNKNOWN_USER", {"userId": call.user_id})
        call.actor = user
        call.actor_role = self.store.attr(user, "role")
        return user

    def _validate_input(self, action: ActionSpec, body: Any) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise _invalid([{"field": None, "problem": "요청 본문은 JSON 객체여야 합니다"}])
        problems = [
            {"field": name, "problem": "정의되지 않은 필드입니다"}
            for name in body
            if name not in action.input
        ]
        values: dict[str, Any] = {}
        for name, spec in action.input.items():
            value = body.get(name)
            values[name] = value
            problem = self._input_problem(spec, value)
            if problem:
                problems.append({"field": name, "problem": problem, "value": value})
        if problems:
            raise _invalid(problems)
        return values

    def _input_problem(self, spec: InputField, value: Any) -> str | None:
        if value is None:
            return "필수값입니다" if spec.required else None
        if spec.type == "int" and (not isinstance(value, int) or isinstance(value, bool)):
            return "정수여야 합니다"
        if spec.type in ("string", "enum") and not isinstance(value, str):
            return "문자열이어야 합니다"
        if spec.type == "enum" and value not in spec.values:
            return f"{', '.join(spec.values)} 중 하나여야 합니다"
        if spec.max_length is not None and len(value) > spec.max_length:
            return f"{spec.max_length}자 이하여야 합니다"
        if spec.ref and self.store.get(spec.ref, value) is None:
            return f"없는 {spec.ref}입니다"
        return None

    def _check_permission(self, action: ActionSpec, call: _Call) -> None:
        if call.actor_role not in action.allowed_roles:
            detail = {"role": call.actor_role, "allowedRoles": sorted(action.allowed_roles)}
            violations = [{"code": "ROLE_NOT_ALLOWED", "detail": detail}]
            message = self.spec.reason_codes["ROLE_NOT_ALLOWED"].message
            raise _Rejected(403, DENIED, "PERMISSION", violations, message)
        self._check(action.permission_criteria, call, status=403, stage="PERMISSION")

    def _check(self, criteria: tuple[Criterion, ...], call: _Call, status: int, stage: str) -> None:
        """조건을 모두 검사하고, 실패한 것이 있으면 순서대로 모아 거부한다."""
        failed: list[Criterion] = []
        for c in criteria:
            if c.when is not None and not c.when.evaluate(self.store, call.names):
                continue
            if not c.check.evaluate(self.store, call.names):
                failed.append(c)
        if not failed:
            return
        violations = [
            {
                "code": c.reason,
                "detail": {
                    k: self._json(expr.evaluate(self.store, call.names))
                    for k, expr in c.detail.items()
                },
            }
            for c in failed
        ]
        first = failed[0]
        if first.message is not None:
            message = first.message.render(self.store, call.names)
        else:
            message = self.spec.reason_codes[first.reason].message
        raise _Rejected(status, DENIED, stage, violations, message)

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------

    def _execute(self, action: ActionSpec, call: _Call, target: Any) -> tuple[int, dict]:
        store, names = self.store, call.names
        created: list[dict] = []
        changes: list[dict] = []
        try:
            with store.transaction():
                # 실행 전에 비교 대상을 고정하고 값을 적어 둔다
                watched = [
                    (obj, name, store.attr(obj, name))
                    for o in action.observe
                    for obj in _as_list(o.objects.evaluate(store, names))
                    for name in o.fields
                ]
                # 같은 객체를 연달아 바꾸는 set은 모아서 한 번에 저장한다. DB의 CHECK 제약
                # (DONE이면 completedAt이 있다, 재가동 시각과 승인자는 함께 있다)을 지키기 위해서다.
                # 그래서 모으는 동안의 식은 바꾸기 전 값을 본다.
                pending: tuple[Any, dict[str, Any]] | None = None
                for effect in action.effects:
                    if effect.when is not None and not effect.when.evaluate(store, names):
                        continue
                    if effect.create is not None:
                        pending = self._flush(pending)
                        obj = store.create(effect.create, self._values(effect.values, names))
                        type_name, object_id = store.ref(obj)
                        created.append({"type": type_name, "id": object_id, **self._json(obj)})
                        continue
                    obj = effect.set_object.evaluate(store, names)
                    if pending is not None and store.ref(pending[0]) != store.ref(obj):
                        pending = self._flush(pending)
                    if pending is None:
                        pending = (obj, {})
                    before = store.attr(obj, effect.set_field)
                    after = effect.set_value.evaluate(store, names)
                    pending[1][effect.set_field] = after
                    changes.append(self._change(obj, effect.set_field, before, after))
                self._flush(pending)
                for obj, name, before in watched:
                    after = store.attr(obj, name)
                    if after != before:
                        changes.append(self._change(obj, name, before, after))
                remaining = {
                    k: self._json(expr.evaluate(store, names))
                    for k, expr in action.remaining.items()
                }
        except StoreConflict as e:
            violations = [{"code": e.reason_code, "detail": {}}]
            message = self.spec.reason_codes[e.reason_code].message
            raise _Rejected(409, DENIED, "PRECONDITION", violations, message) from None

        type_name, object_id = store.ref(target)
        body = {
            "action": action.name,
            "result": ALLOWED,
            "at": self._json(call.at),
            "actor": {"userId": store.attr(call.actor, "userId"), "role": call.actor_role},
            "target": {"type": type_name, "id": object_id},
            "created": created,
            "changes": changes,
            "remaining": remaining,
        }
        return (201 if created else 200), body

    def _flush(self, pending: tuple[Any, dict[str, Any]] | None) -> None:
        if pending is not None:
            self.store.update(*pending)

    def _values(self, values: Values, names: Mapping[str, Any]) -> dict[str, Any]:
        return {
            k: self._values(v, names) if isinstance(v, Mapping) else v.evaluate(self.store, names)
            for k, v in values.items()
        }

    def _change(self, obj: Any, field_name: str, before: Any, after: Any) -> dict[str, Any]:
        type_name, object_id = self.store.ref(obj)
        return {
            "type": type_name,
            "id": object_id,
            "field": field_name,
            "before": self._json(before),
            "after": self._json(after),
        }

    def _json(self, value: Any) -> Any:
        """응답에 넣을 수 있는 값으로 바꾼다. 객체는 저장 속성만 담는다."""
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, Mapping):
            return {k: self._json(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._json(v) for v in value]
        if self.store.is_object(value):
            return self._json(self.store.properties(value))
        return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def _common(status: int, result: str, stage: str, code: str, detail: dict) -> _Rejected:
    violations = [{"code": code, "detail": detail}]
    return _Rejected(status, result, stage, violations, COMMON_MESSAGES[code])


def _invalid(problems: list[dict]) -> _Rejected:
    violations = [{"code": "INVALID_INPUT", "detail": p} for p in problems]
    return _Rejected(422, ERROR, "INPUT", violations, COMMON_MESSAGES["INVALID_INPUT"])
