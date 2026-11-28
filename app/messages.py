"""actions.yaml 밖에서 쓰는 공통 코드의 메시지 (docs/api.md 1장 공통 코드).

액션 엔진(app/action/engine.py)과 API 공통 응답(app/api/common.py)이 함께 가져다 쓴다.
FastAPI에 기대지 않도록 따로 둔다.
"""

COMMON_MESSAGES = {
    "UNKNOWN_USER": "사용자를 확인할 수 없습니다",
    "NOT_FOUND": "대상을 찾을 수 없습니다",
    "INVALID_INPUT": "입력값이 올바르지 않습니다",
    "INVALID_FILTER": "조회 조건이 올바르지 않습니다",
    "METHOD_NOT_ALLOWED": "이 경로는 이 메서드를 받지 않습니다",
    "HTTP_ERROR": "요청을 처리할 수 없습니다",
}
