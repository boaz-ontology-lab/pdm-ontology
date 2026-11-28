"""식 평가기(app/action/expr.py)의 Resolver를 조회 엔진으로 구현한다.

평가기가 다루는 객체는 ObjectRecord다. 객체를 찾거나, 링크를 따라가거나,
파생 속성을 읽을 때마다 DB에 묻는다.
조회 엔진의 QueryError는 식 평가 오류이므로 ExprError로 바꿔 올린다.
"""

from __future__ import annotations

from typing import Any

from app.action.expr import ExprError
from app.engine.engine import LinkCardinalityError, ObjectRecord, QueryEngine
from app.engine.query import QueryError

_KEY_TYPES: dict[str, tuple[type, ...]] = {"int": (int,), "string": (str,)}


class EngineResolver:
    def __init__(self, engine: QueryEngine) -> None:
        self.engine = engine

    def has_type(self, type_name: str) -> bool:
        return self.engine.ontology.object(type_name) is not None

    def get(self, type_name: str, object_id: Any) -> ObjectRecord | None:
        key = self.engine.object_type(type_name).key
        allowed = _KEY_TYPES.get(key.type, (object,))
        if isinstance(object_id, bool) or not isinstance(object_id, allowed):
            raise ExprError(f"{type_name}의 id는 {key.type}입니다 ({object_id!r})")
        try:
            return self.engine.get(type_name, object_id)
        except QueryError as e:
            raise ExprError(e.message) from None

    def attr(self, obj: Any, name: str) -> Any:
        if not isinstance(obj, ObjectRecord):
            raise ExprError(f"{type(obj).__name__} 값에는 속성 '{name}'이 없습니다")
        if name in obj.properties:
            return obj.properties[name]
        if name in self.engine.object_type(obj.type).derived:
            # obj.derived는 읽은 시점의 값이다. effect 뒤에도 새 값이 보이게 매번 계산한다
            try:
                return self.engine.derived_value(obj, name)
            except LookupError as e:
                raise ExprError(str(e)) from None
        if self.engine.ontology.link(obj.type, name) is not None:
            try:
                return self.engine.follow(obj, name)
            except LinkCardinalityError as e:
                raise ExprError(str(e)) from None
        raise ExprError(f"{obj.type}에 '{name}'이 없습니다")
