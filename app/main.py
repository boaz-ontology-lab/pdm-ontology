"""FastAPI 앱을 만든다.

create_app은 요청마다 세션(엔진 묶음)을 여는 팩토리를 받는다 (app/api/common.py Session).
엔진 하나를 앱 전체가 나눠 쓰면, DB 연결 하나에 동시 요청의 트랜잭션이 섞인다.
조회 라우터는 #18에서 B가 붙인다 (Session.query, QueryError 핸들러는 body_error_handler).
서버를 띄우는 진입점(uvicorn --factory)은 DB 저장소 PR에서 여기에 더한다.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.action.router import router as action_router
from app.api.common import SessionFactory, install_error_handlers


def create_app(sessions: SessionFactory) -> FastAPI:
    app = FastAPI(
        title="pdm-ontology",
        description="온톨로지 조회와 액션을 하나의 API로 연다. 실행자는 X-User-Id 헤더로 보낸다.",
    )
    install_error_handlers(app)
    app.state.sessions = sessions
    app.include_router(action_router, tags=["action"])
    return app
