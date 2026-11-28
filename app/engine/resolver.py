"""식 평가기(app/action/expr.py)의 Resolver를 조회 엔진으로 구현한다.

평가기가 다루는 객체는 ObjectRecord다. 객체를 찾거나 링크를 따라갈 때마다 DB에 묻는다.
조회 엔진의 QueryError는 식 평가 오류이므로 ExprError로 바꿔 올린다.
"""

from __future__ import annotations

from typing import Any

from app.action.expr import ExprError
from app.engine.engine import ObjectRecord, QueryEngine
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
        if name in obj.derived:
            return obj.derived[name]
        obj_type = self.engine.object_type(obj.type)
        if name in obj_type.derived:
            # #17에서 파생 속성을 SQL로 계산하면 여기서 돌려준다
            raise ExprError(f"{obj.type}.{name}은 파생 속성이라 아직 계산하지 않습니다")
        if self.engine.ontology.link(obj.type, name) is not None:
            try:
                return self.engine.follow(obj, name)
            except LookupError as e:
                # N:1 링크에 여러 건이 걸렸다. 스펙이나 데이터가 틀린 경우다
                raise ExprError(str(e)) from None
        raise ExprError(f"{obj.type}에 '{name}'이 없습니다")
