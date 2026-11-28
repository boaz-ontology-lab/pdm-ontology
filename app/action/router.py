"""액션 API와 감사 로그 API (docs/api.md 3장, 4장).

라우터는 요청을 엔진에 넘기고 엔진이 정한 HTTP 코드와 본문을 그대로 돌려준다.
검사 순서, 감사 로그 기록은 모두 엔진이 한다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.action.store import AuditEntry
from app.api.common import ApiError, CurrentUser, SessionDep, user_id

router = APIRouter()

# GET /audit 필터: 쿼리 이름 → AuditRecord 필드
AUDIT_FILTERS = {
    "targetType": "target_type",
    "targetId": "target_id",
    "actorId": "actor_id",
    "action": "action",
    "result": "result",
}
AUDIT_RESULTS = frozenset({"ALLOWED", "DENIED", "ERROR"})
INTEGER = re.compile(r"-?[0-9]+")  # isdigit()은 위 첨자 같은 문자도 받는다


@router.post(
    "/actions/{actionName}",
    summary="액션 실행",
    description=(
        "본문은 ontology/actions.yaml의 input 필드다. 실행자는 X-User-Id 헤더로 보낸다. "
        "허용 200/201, 사용자 확인 401, 권한 403, 대상 없음 404, 사전조건 409, 입력 422."
    ),
)
async def run_action(
    action_name: Annotated[str, Path(alias="actionName")], request: Request, session: SessionDep
) -> Response:
    try:
        body: Any = await request.json()
    except ValueError:
        body = None  # 본문이 없거나 JSON이 아니면 엔진이 422로 거부하고 감사 로그에 남긴다
    engine = session.action
    result = await run_in_threadpool(engine.execute, action_name, user_id(request), body)
    return JSONResponse(result.body, status_code=result.status)


@router.get(
    "/audit",
    summary="감사 로그 조회",
    description="필터: targetType, targetId, actorId, action, result. actionId 순서로 돌려준다.",
)
def list_audit(request: Request, session: SessionDep, user: CurrentUser) -> Response:
    # def라서 FastAPI가 threadpool에서 돌린다. 저장소 호출이 이벤트 루프를 막지 않는다
    filters: dict[str, Any] = {}
    problems: list[dict[str, Any]] = []
    for name, value in request.query_params.items():
        if name not in AUDIT_FILTERS:
            problems.append({"field": name, "problem": "없는 필터입니다", "value": value})
        elif name == "targetId":
            target_id = _integer(value)
            if target_id is None:
                problems.append({"field": name, "problem": "정수여야 합니다", "value": value})
            else:
                filters["target_id"] = target_id
        elif name == "result" and value not in AUDIT_RESULTS:
            problems.append(
                {"field": name, "problem": "ALLOWED, DENIED, ERROR 중 하나", "value": value}
            )
        else:
            filters[AUDIT_FILTERS[name]] = value
    if problems:
        violations = [{"code": "INVALID_FILTER", "detail": p} for p in problems]
        raise ApiError(422, "ERROR", "INPUT", "INVALID_FILTER", violations)

    items = [_audit_item(e) for e in session.action.store.find_audit(filters)]
    return JSONResponse({"total": len(items), "items": items})


def _integer(value: str) -> int | None:
    """정수 문자열이면 int. "--5", "²", "1_7", 정수 자릿수 한도를 넘는 값은 None."""
    if not INTEGER.fullmatch(value):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _audit_item(entry: AuditEntry) -> dict[str, Any]:
    r = entry.record
    return {
        "actionId": entry.action_id,
        "action": r.action,
        "result": r.result,
        "stage": r.stage,
        "reasonCode": r.reason_code,
        "actor": {"userId": r.actor_id, "role": r.actor_role},
        "target": {"type": r.target_type, "id": r.target_id} if r.target_type else None,
        "input": r.input,
        "response": r.response,
        "at": _iso(r.at),
        "recordedAt": _iso(entry.recorded_at),
    }


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
