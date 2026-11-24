"""액션 엔진이 데이터를 읽고 쓸 때 부르는 인터페이스.

읽기는 식 평가기의 Resolver와 같다. 엔진은 여기에 쓰기, 트랜잭션, 감사 로그를 더해 쓴다.
DB 구현은 조회 엔진(B)과 ops 스키마(A)가 준비되면 붙인다. 지금은 테스트의 메모리 저장소가 구현한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.action.expr import Resolver


class StoreConflict(Exception):
    """DB 제약(같은 설비에 REQUESTED 점검 요청 둘 등)에 걸렸다. 엔진은 409로 바꿔 돌려준다.

    동시 요청이 엔진의 사전조건 검사를 함께 통과했을 때만 일어난다.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AuditRecord:
    """ops.audit_log 한 행. 호출마다 결과와 상관없이 하나 남긴다."""

    action: str
    result: str  # ALLOWED | DENIED | ERROR
    stage: str | None  # ALLOWED면 None
    reason_code: str | None
    actor_id: str | None
    actor_role: str | None
    target_type: str | None
    target_id: int | None
    input: Mapping[str, Any]
    response: Mapping[str, Any]  # 응답 본문. actionId는 이 행의 번호라 넣지 않는다
    at: datetime  # 가상 시계 시각


class Store(Resolver, Protocol):
    def is_object(self, value: Any) -> bool:
        """값이 객체인가 (목록, 숫자, 문자열이 아닌가)."""

    def ref(self, obj: Any) -> tuple[str, Any]:
        """(객체 타입, id)."""

    def properties(self, obj: Any) -> dict[str, Any]:
        """저장 속성만. 응답의 created, detail, remaining에 쓴다."""

    def create(self, type_name: str, values: Mapping[str, Any]) -> Any:
        """객체를 새로 만들고 돌려준다. id는 저장소가 붙인다. 제약에 걸리면 StoreConflict."""

    def update(self, obj: Any, values: Mapping[str, Any]) -> None:
        """저장 속성 여러 개를 한 번에 바꾼다. 파생 속성이면 오류. 제약에 걸리면 StoreConflict.

        DB 제약은 함께 바뀌는 값을 모두 넣은 뒤에 검사된다. 예를 들어 작업지시의 status를 DONE으로,
        completedAt을 now로 바꾸는 일은 한 번의 update여야 한다 (db/05_ops.sql의 CHECK).
        """

    def transaction(self) -> AbstractContextManager[None]:
        """블록 안에서 예외가 나면 create, set을 모두 되돌린다."""

    def append_audit(self, record: AuditRecord) -> int:
        """감사 로그를 한 행 추가하고 actionId를 돌려준다. 액션 트랜잭션과 따로 커밋한다."""
