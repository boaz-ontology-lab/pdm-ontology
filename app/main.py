"""FastAPI 앱을 만든다.

조회 라우터(B, #18)는 여기에 include_router 한 줄로 붙인다.
액션 엔진의 DB 저장소가 생기면, 서버를 띄우는 진입점(uvicorn --factory)도 여기에 둔다.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.action.engine import ActionEngine
from app.action.router import router as action_router
from app.api.common import install_error_handlers


def create_app(action_engine: ActionEngine) -> FastAPI:
    app = FastAPI(
        title="pdm-ontology",
        description="온톨로지 조회와 액션을 하나의 API로 연다. 실행자는 X-User-Id 헤더로 보낸다.",
    )
    install_error_handlers(app)
    app.state.action_engine = action_engine
    app.include_router(action_router, tags=["action"])
    return app
