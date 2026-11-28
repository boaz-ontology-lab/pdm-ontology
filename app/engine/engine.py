"""조회 엔진. 객체 하나(get)와 객체 목록(list)을 DB에서 읽는다.

응답 모양은 docs/api.md 2절 "객체 표현"이다. derived(#17)와 masked(#19)는 아직 비어 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.engine import query as q
from app.engine.query import ListQuery, QueryError, Statement
from app.engine.spec import ObjectType, Ontology


@dataclass(frozen=True)
class ObjectRecord:
    type: str
    id: Any
    properties: Mapping[str, Any]
    derived: Mapping[str, Any] = field(default_factory=dict)
    masked: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "properties": {k: _json(v) for k, v in self.properties.items()},
            "derived": {k: _json(v) for k, v in self.derived.items()},
            "masked": list(self.masked),
        }


@dataclass(frozen=True)
class Page:
    type: str
    total: int
    items: tuple[ObjectRecord, ...]
    statement: Statement  # explain용. QueryEngine.render로 문자열로 바꾼다

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "total": self.total, "items": [i.to_json() for i in self.items]}


class QueryEngine:
    def __init__(self, ontology: Ontology, conn: psycopg.Connection) -> None:
        self.ontology = ontology
        self.conn = conn

    def object_type(self, type_name: str) -> ObjectType:
        obj = self.ontology.object(type_name)
        if obj is None:
            raise QueryError("NOT_FOUND", f"객체 타입 {type_name}이 없습니다")
        return obj

    def get(self, type_name: str, object_id: Any) -> ObjectRecord | None:
        """primary key로 객체 하나를 찾는다. 없으면 None. 경로에서 온 문자열 id도 받는다."""
        obj = self.object_type(type_name)
        key = obj.key
        if isinstance(object_id, str) and key.type != "string":
            try:
                object_id = q.coerce(key, object_id)
            except QueryError:
                message = f"{type_name}의 id는 {key.type}입니다 ('{object_id}')"
                detail = {"field": "id", "problem": message, "value": object_id}
                raise QueryError(
                    "INVALID_INPUT", message, [{"code": "INVALID_INPUT", "detail": detail}]
                ) from None
        rows = self._fetch(q.select_by_id(obj, object_id))
        return self._record(obj, rows[0]) if rows else None

    def list(self, type_name: str, params: Mapping[str, str] | ListQuery | None = None) -> Page:
        """객체 목록. params는 쿼리 문자열 그대로(필터, sort, limit, offset)이거나 ListQuery다."""
        obj = self.object_type(type_name)
        if not isinstance(params, ListQuery):
            params = q.parse_list_query(obj, params or {})
        total = self._fetch(q.count_list(obj, params))[0]["count"]
        statement = q.select_list(obj, params)
        items = tuple(self._record(obj, r) for r in self._fetch(statement))
        return Page(type=type_name, total=total, items=items, statement=statement)

    def render(self, statement: Statement) -> str:
        """파라미터를 채운 SQL 문자열.

        explain에서 사람에게 보여 주는 용도이고 실행에는 쓰지 않는다.
        """
        return psycopg.ClientCursor(self.conn).mogrify(statement.sql, statement.params)

    def _fetch(self, statement: Statement) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(statement.sql, statement.params)
            return cur.fetchall()

    @staticmethod
    def _record(obj: ObjectType, row: Mapping[str, Any]) -> ObjectRecord:
        return ObjectRecord(type=obj.name, id=row[obj.primary_key], properties=dict(row))


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
