"""액션 API와 감사 로그 API (docs/api.md 3장, 4장)

라우터는 요청을 엔진에 넘기고 엔진이 정한 HTTP 코드와 본문을 그대로 돌려준다.
검사 순서, 감사 로그 기록은 모두 엔진이 한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.action.engine import ActionEngine
from app.action.store import AuditEntry
from app.api.common import failure, user_id

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


def get_action_engine(request: Request) -> ActionEngine:
    """앱을 만들 때 app.state.action_engine에 넣은 엔진 (app/main.py)."""
    return request.app.state.action_engine


Engine = Annotated[ActionEngine, Depends(get_action_engine)]


@router.post(
    "/actions/{action_name}",
    summary="액션 실행",
    description=(
        "본문은 ontology/actions.yaml의 input 필드다. 실행자는 X-User-Id 헤더로 보낸다. "
        "허용 200/201, 사용자 확인 401, 권한 403, 대상 없음 404, 사전조건 409, 입력 422."
    ),
)
async def run_action(action_name: str, request: Request, engine: Engine) -> Response:
    try:
        body: Any = await request.json()
    except ValueError:
        body = None  # 본문이 없거나 JSON이 아니면 엔진이 422로 거부하고 감사 로그에 남긴다
    result = await run_in_threadpool(engine.execute, action_name, user_id(request), body)
    return JSONResponse(result.body, status_code=result.status)


@router.get(
    "/audit",
    summary="감사 로그 조회",
    description="필터: targetType, targetId, actorId, action, result. actionId 순서로 돌려준다.",
)
async def list_audit(request: Request, engine: Engine) -> Response:
    uid = user_id(request)
    if uid is None or engine.store.get("User", uid) is None:
        return failure(401, "ERROR", "AUTH", "UNKNOWN_USER", [
            {"code": "UNKNOWN_USER", "detail": {"userId": uid}}
        ])  # fmt: skip

    filters: dict[str, Any] = {}
    problems: list[dict[str, Any]] = []
    for name, value in request.query_params.items():
        if name not in AUDIT_FILTERS:
            problems.append({"field": name, "problem": "없는 필터입니다"})
        elif name == "targetId" and not value.lstrip("-").isdigit():
            problems.append({"field": name, "problem": "정수여야 합니다", "value": value})
        elif name == "result" and value not in AUDIT_RESULTS:
            problems.append(
                {"field": name, "problem": "ALLOWED, DENIED, ERROR 중 하나", "value": value}
            )
        else:
            filters[AUDIT_FILTERS[name]] = int(value) if name == "targetId" else value
    if problems:
        violations = [{"code": "INVALID_FILTER", "detail": p} for p in problems]
        return failure(422, "ERROR", "INPUT", "INVALID_FILTER", violations)

    entries = await run_in_threadpool(engine.store.find_audit, filters)
    items = [_audit_item(e) for e in entries]
    return JSONResponse({"total": len(items), "items": items})


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
