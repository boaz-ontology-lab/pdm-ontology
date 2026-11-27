"""조회 API와 액션 API가 함께 사용하는 응답이다. (docs/api.md 1장)

- 실패 응답 모양: 401, 403, 404, 409, 422 모두 result, stage, reasonCode, message, violations
- 실행자: X-User-Id 헤더. 역할은 헤더로 받지 않고 서버가 찾는다.
- FastAPI 기본 422({"detail": [...]})와 404({"detail": "Not Found"})를 위 모양으로 바꾼다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

USER_HEADER = "X-User-Id"

MESSAGES = {
    "UNKNOWN_USER": "사용자를 확인할 수 없습니다",
    "NOT_FOUND": "대상을 찾을 수 없습니다",
    "INVALID_INPUT": "입력값이 올바르지 않습니다",
    "INVALID_FILTER": "조회 조건이 올바르지 않습니다",
}


def failure(
    status: int,
    result: str,
    stage: str,
    code: str,
    violations: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> JSONResponse:
    """실패 응답. violations를 주지 않으면 code 하나만 담는다."""
    body = {
        "result": result,
        "stage": stage,
        "reasonCode": code,
        "message": message or MESSAGES[code],
        "violations": violations or [{"code": code, "detail": {}}],
    }
    return JSONResponse(body, status_code=status)


def user_id(request: Request) -> str | None:
    """X-User-Id 헤더 값. 비어 있으면 None."""
    return request.headers.get(USER_HEADER) or None


async def _on_validation_error(request: Request, exc: RequestValidationError) -> Response:
    violations = [
        {
            "code": "INVALID_INPUT",
            "detail": {
                "field": ".".join(str(p) for p in e["loc"][1:]) or None,
                "problem": e["msg"],
            },
        }
        for e in exc.errors()
    ]
    return failure(422, "ERROR", "INPUT", "INVALID_INPUT", violations)


async def _on_http_error(request: Request, exc: HTTPException) -> Response:
    if exc.status_code == 404:
        return failure(404, "ERROR", "INPUT", "NOT_FOUND", [{"code": "NOT_FOUND", "detail": {}}])
    return await http_exception_handler(request, exc)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(HTTPException, _on_http_error)
