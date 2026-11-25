"""objects.yaml의 derived.sql 자리표시자를 펼쳐 SQL 식으로 만든다 (결정 20).

자리표시자 규칙은 objects.yaml 머리 주석을 따른다.

    {now}               가상 시계 시각. 문장 앞의 WITH clock(now)에서 읽는다 (query.with_clock)
    {이름}              이 객체의 속성(별칭.컬럼)이나 파생 속성(그 sql을 괄호로 감싸 펼친다)
    {객체타입} 별칭      그 객체의 source
    {별칭.파생속성}      별칭 객체의 파생 속성

자리표시자가 아닌 중괄호('[{"rule": ...}]' 같은 JSON)는 그대로 둔다.
스펙을 읽을 때(spec.build) 한 번 펼쳐 두고, 틀린 이름이나 순환은 그때 SpecError로 알린다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from psycopg import sql

if TYPE_CHECKING:
    from app.engine.spec import ObjectType

ROW_ALIAS = "t"  # 조회하는 객체 행의 별칭. query.py도 같은 이름을 쓴다
PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)(?:\.([A-Za-z][A-Za-z0-9]*))?\}")
ALIAS_DECL = re.compile(r"\{([A-Z][A-Za-z0-9]*)\}\s+([a-z][a-z0-9_]*)")
NOW = sql.SQL("(SELECT now FROM clock)")


class DerivedError(Exception):
    pass


def expand(
    objects: Mapping[str, ObjectType],
    obj: ObjectType,
    name: str,
    alias: str = ROW_ALIAS,
    _stack: tuple[tuple[str, str], ...] = (),
) -> sql.Composed:
    """obj.name 파생 속성의 sql을 펼친다. alias는 그 객체 행을 가리키는 SQL 별칭이다."""
    key = (obj.name, name)
    if key in _stack:
        path = " → ".join(f"{t}.{n}" for t, n in (*_stack, key))
        raise DerivedError(f"파생 속성이 서로를 부릅니다: {path}")
    stack = (*_stack, key)
    text = obj.derived[name].sql
    aliases = {a: t for t, a in ALIAS_DECL.findall(text)}

    parts: list[sql.Composable] = []
    pos = 0
    for m in PLACEHOLDER.finditer(text):
        parts.append(_text(text[pos : m.start()]))
        parts.append(_placeholder(objects, obj, alias, aliases, m[1], m[2], stack))
        pos = m.end()
    parts.append(_text(text[pos:]))
    return sql.Composed(parts)


def _placeholder(
    objects: Mapping[str, ObjectType],
    obj: ObjectType,
    alias: str,
    aliases: Mapping[str, str],
    head: str,
    tail: str | None,
    stack: tuple[tuple[str, str], ...],
) -> sql.Composable:
    if tail is not None:
        if head not in aliases:
            raise DerivedError(f"{{{head}.{tail}}}: 별칭 {head}을 선언하지 않았습니다")
        other = objects.get(aliases[head])
        if other is None:
            raise DerivedError(f"객체 타입 {aliases[head]}이 없습니다")
        if tail not in other.derived:
            raise DerivedError(f"{{{head}.{tail}}}: {other.name}에 파생 속성 {tail}이 없습니다")
        return _paren(expand(objects, other, tail, head, stack))
    if head == "now":
        return NOW
    if head in objects:
        schema, table = objects[head].source.split(".")
        return sql.Identifier(schema, table)
    if head in obj.properties:
        return sql.Identifier(alias, obj.properties[head].column)
    if head in obj.derived:
        return _paren(expand(objects, obj, head, alias, stack))
    raise DerivedError(f"{{{head}}}: {obj.name}에 속성이나 파생 속성 {head}이 없습니다")


def _text(chunk: str) -> sql.SQL:
    # 문장을 파라미터와 함께 실행하므로 %는 %%로 적어야 글자 그대로 남는다
    return sql.SQL(chunk.replace("%", "%%"))


def _paren(expr: sql.Composable) -> sql.Composed:
    return sql.Composed([sql.SQL("("), expr, sql.SQL(")")])
