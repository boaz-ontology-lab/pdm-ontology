"""조회 API와 액션 API가 함께 쓰는 것 (docs/api.md 1장).

- 실패 응답 모양: 401, 403, 404, 405, 409, 422 모두 result, stage, reasonCode, message, violations
- 요청 세션: 요청마다 엔진 묶음(Session)을 열고 끝나면 닫는다. DB 연결을 요청끼리 나누지 않는다
- 실행자: X-User-Id 헤더. 역할은 헤더로 받지 않고 서버가 찾는다 (current_user)
- FastAPI 기본 422({"detail": [...]})와 HTTPException({"detail": ...})을 위 모양으로 바꾼다
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from app.messages import COMMON_MESSAGES

if TYPE_CHECKING:
    from app.action.engine import ActionEngine

USER_HEADER = "X-User-Id"


# ---- 실패 응답 ---------------------------------------------------------------


def failure_body(
    result: str,
    stage: str,
    code: str,
    violations: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """실패 응답 본문. violations를 주지 않으면 code 하나만 담는다."""
    return {
        "result": result,
        "stage": stage,
        "reasonCode": code,
        "message": message or COMMON_MESSAGES[code],
        "violations": violations or [{"code": code, "detail": {}}],
    }


def failure(
    status: int,
    result: str,
    stage: str,
    code: str,
    violations: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> JSONResponse:
    return JSONResponse(failure_body(result, stage, code, violations, message), status_code=status)


class BodyError(Protocol):
    """status와 to_body()를 가진 예외. ApiError와 조회 엔진의 QueryError가 이 모양이다."""

    status: int

    def to_body(self) -> dict[str, Any]: ...


class ApiError(Exception):
    """라우터나 Depends에서 실패 응답으로 끝낼 때 던진다."""

    def __init__(
        self,
        status: int,
        result: str,
        stage: str,
        code: str,
        violations: list[dict[str, Any]] | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.body = failure_body(result, stage, code, violations, message)

    def to_body(self) -> dict[str, Any]:
        return self.body


async def body_error_handler(request: Request, exc: BodyError) -> Response:
    """BodyError를 응답으로 바꾼다. 조회 라우터는 app.add_exception_handler(QueryError, 이것)."""
    return JSONResponse(exc.to_body(), status_code=exc.status)


# ---- 요청 세션 ---------------------------------------------------------------


@dataclass
class Session:
    """요청 하나가 쓰는 엔진 묶음. 조회 엔진(query)은 #18에서 B가 더한다."""

    action: ActionEngine


# 부를 때마다 세션 하나를 열고 닫는 context manager를 돌려준다. DB면 연결 하나를 연다
SessionFactory = Callable[[], AbstractContextManager[Session]]


def fixed_sessions(session: Session) -> SessionFactory:
    """매 요청 같은 세션을 쓴다. 메모리 저장소 테스트용."""

    @contextmanager
    def open_session() -> Iterator[Session]:
        yield session

    return open_session


def get_session(request: Request) -> Iterator[Session]:
    """app.state.sessions(app/main.py)로 요청마다 세션을 열고, 응답 뒤 닫는다."""
    with request.app.state.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


# ---- 실행자 ------------------------------------------------------------------


def user_id(request: Request) -> str | None:
    """X-User-Id 헤더 값. 비어 있으면 None."""
    return request.headers.get(USER_HEADER) or None


def current_user(request: Request, session: SessionDep) -> Any:
    """헤더의 사용자 객체. 없으면 401. 역할과 vendorId는 이 객체에서 읽는다.

    액션 실행(POST /actions)은 401도 감사 로그에 남겨야 해서 엔진이 직접 확인한다.
    응답 모양은 같다.
    """
    uid = user_id(request)
    user = session.action.store.get("User", uid) if uid else None
    if user is None:
        raise ApiError(401, "ERROR", "AUTH", "UNKNOWN_USER", [
            {"code": "UNKNOWN_USER", "detail": {"userId": uid}}
        ])  # fmt: skip
    return user


CurrentUser = Annotated[Any, Depends(current_user)]


# ---- FastAPI 기본 오류를 같은 모양으로 ---------------------------------------

# pydantic 오류 종류 → 문구. 여기 없는 종류는 pydantic 문장을 그대로 쓴다
_PROBLEMS: dict[str, Callable[[dict[str, Any]], str]] = {
    "missing": lambda ctx: "필수값입니다",
    "int_parsing": lambda ctx: "정수여야 합니다",
    "int_type": lambda ctx: "정수여야 합니다",
    "string_type": lambda ctx: "문자열이어야 합니다",
    "bool_parsing": lambda ctx: "true 또는 false여야 합니다",
    "greater_than_equal": lambda ctx: f"{ctx['ge']} 이상이어야 합니다",
    "greater_than": lambda ctx: f"{ctx['gt']}보다 커야 합니다",
    "less_than_equal": lambda ctx: f"{ctx['le']} 이하여야 합니다",
    "less_than": lambda ctx: f"{ctx['lt']}보다 작아야 합니다",
    "literal_error": lambda ctx: f"{ctx['expected']} 중 하나여야 합니다",
    "enum": lambda ctx: f"{ctx['expected']} 중 하나여야 합니다",
    "json_invalid": lambda ctx: "JSON 형식이 아닙니다",
}


def _validation_detail(error: dict[str, Any]) -> dict[str, Any]:
    """조회 엔진의 detail(field, problem, value)과 같은 모양."""
    to_problem = _PROBLEMS.get(error["type"])
    missing = error["type"] == "missing"
    return {
        "field": ".".join(str(p) for p in error["loc"][1:]) or None,
        "problem": to_problem(error.get("ctx") or {}) if to_problem else error["msg"],
        "value": None if missing else jsonable_encoder(error.get("input")),
    }


async def _on_validation_error(request: Request, exc: RequestValidationError) -> Response:
    violations = [{"code": "INVALID_INPUT", "detail": _validation_detail(e)} for e in exc.errors()]
    return failure(422, "ERROR", "INPUT", "INVALID_INPUT", violations)


# HTTPException 상태 → 공통 코드. 문구는 COMMON_MESSAGES로 고정하고 Starlette의 영어 detail은 버린다
_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


async def _on_http_error(request: Request, exc: HTTPException) -> Response:
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    response = failure(exc.status_code, "ERROR", "INPUT", code)
    if exc.headers:  # 405의 Allow 같은 헤더는 그대로 둔다
        response.headers.update(exc.headers)
    return response


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(HTTPException, _on_http_error)
    app.add_exception_handler(ApiError, body_error_handler)
