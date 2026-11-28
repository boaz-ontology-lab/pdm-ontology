"""조회 엔진. 객체 하나(get), 객체 목록(list), 링크 따라가기(follow, links)를 DB에서 읽는다.

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
from app.engine.spec import Link, ObjectType, Ontology


class LinkCardinalityError(LookupError):
    """객체 하나로 이어져야 할 링크에 여러 건이 걸렸다. 스펙이나 데이터가 틀린 경우다."""


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


@dataclass(frozen=True)
class LinkPage:
    """GET /objects/{type}/{id}/links/{link}의 응답. 객체 하나로 이어지는 링크도 목록으로 준다."""

    source: ObjectRecord
    link: Link
    total: int
    items: tuple[ObjectRecord, ...]
    statement: Statement

    def to_json(self) -> dict[str, Any]:
        return {
            "from": {"type": self.source.type, "id": self.source.id},
            "link": self.link.name,
            "to": self.link.to_type,
            "total": self.total,
            "items": [i.to_json() for i in self.items],
        }


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

    def link(self, type_name: str, link_name: str) -> Link:
        link = self.ontology.link(self.object_type(type_name).name, link_name)
        if link is None:
            raise QueryError("NOT_FOUND", f"{type_name}에 링크 {link_name}이 없습니다")
        return link

    def follow(
        self, source: ObjectRecord, link_name: str
    ) -> list[ObjectRecord] | ObjectRecord | None:
        """식 평가기가 x.링크를 부를 때.

        1:N이면 목록(primary key 오름차순), 아니면 객체 하나나 None.
        """
        link = self.link(source.type, link_name)
        items = self._link_items(source, link, q.ALL)
        if link.many:
            return list(items)
        if len(items) > 1:
            raise LinkCardinalityError(
                f"{source.type}({source.id}).{link.name}은 {link.cardinality}인데 "
                f"{len(items)}건이 이어집니다"
            )
        return items[0] if items else None

    def links(
        self,
        type_name: str,
        object_id: Any,
        link_name: str,
        params: Mapping[str, str] | ListQuery | None = None,
    ) -> LinkPage:
        """링크 API. params는 도착 객체 타입의 필터, sort, limit, offset이다."""
        link = self.link(type_name, link_name)
        source = self.get(type_name, object_id)
        if source is None:
            raise QueryError("NOT_FOUND", f"{type_name} {object_id}이 없습니다")
        if not isinstance(params, ListQuery):
            params = q.parse_list_query(self.object_type(link.to_type), params or {})
        scope = self._link_scope(source, link)
        to = self.object_type(link.to_type)
        total = self._fetch(q.count_list(to, params, scope))[0]["count"]
        statement = q.select_list(to, params, scope)
        items = tuple(self._record(to, r) for r in self._fetch(statement))
        return LinkPage(source=source, link=link, total=total, items=items, statement=statement)

    def _link_items(
        self, source: ObjectRecord, link: Link, params: ListQuery
    ) -> tuple[ObjectRecord, ...]:
        to = self.object_type(link.to_type)
        statement = q.select_list(to, params, self._link_scope(source, link))
        return tuple(self._record(to, r) for r in self._fetch(statement))

    def _link_scope(self, source: ObjectRecord, link: Link) -> q.Clause:
        original_from, original_to = (
            (link.to_type, link.from_type) if link.inverse_of else (link.from_type, link.to_type)
        )
        ends = {"from": self.object_type(original_from), "to": self.object_type(original_to)}
        return q.link_scope(link, ends, source.properties)

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
