"""조회 요청(필터, 정렬, 페이지)을 읽고 SQL로 바꾼다. DB에는 붙지 않는다.

- 값은 모두 파라미터(%s)로 넘긴다. 이름은 스펙에 있는 것만 sql.Identifier로 넣는다.
- 파생 속성도 저장 속성처럼 고르고, 거르고, 정렬한다. 그 자리에 derived.sql을 펼친 식이 들어간다.
  {now}는 문장 앞의 WITH clock(now)에서 읽는다. with_clock으로 감싸야 실행할 수 있다.
- 벤더 행 필터(#19)는 _where에 붙인다.
- 링크를 따라갈 때는 link_scope가 만든 조건을 목록 조회의 WHERE에 더한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql

from app.engine.derived import ROW_ALIAS
from app.engine.spec import Derived, Link, ObjectType, Property, Side

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
RESERVED = frozenset({"sort", "limit", "offset", "explain"})  # 필터가 아닌 쿼리 파라미터
ORDERED_TYPES = frozenset({"int", "float", "datetime"})  # __gte, __lte를 쓸 수 있는 타입
OPERATORS = {"eq": "=", "gte": ">=", "lte": "<="}  # in은 = ANY(%s)로 따로 만든다


class QueryError(Exception):
    """조회 요청이 틀렸다. to_body()가 docs/api.md 1절의 실패 응답 모양이다.

    violations에는 실패한 것을 모두 담는다. 비어 있으면 code 하나로 채운다.
    """

    STATUS = {"NOT_FOUND": 404, "INVALID_FILTER": 422, "INVALID_INPUT": 422}

    def __init__(
        self, code: str, message: str, violations: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.violations = violations or [{"code": code, "detail": {}}]

    @property
    def status(self) -> int:
        return self.STATUS[self.code]

    def to_body(self) -> dict[str, Any]:
        return {
            "result": "ERROR",
            "stage": "INPUT",
            "reasonCode": self.code,
            "message": self.message,
            "violations": self.violations,
        }


@dataclass(frozen=True)
class Condition:
    prop: str
    op: str  # eq | gte | lte | in
    value: Any  # in이면 list


@dataclass(frozen=True)
class SortKey:
    prop: str
    descending: bool = False


@dataclass(frozen=True)
class ListQuery:
    filters: tuple[Condition, ...] = ()
    sort: tuple[SortKey, ...] = ()
    limit: int | None = DEFAULT_LIMIT  # None이면 전부 (LIMIT NULL). 식 평가기가 링크에서 쓴다
    offset: int = 0


ALL = ListQuery(limit=None)


@dataclass(frozen=True)
class Clause:
    """WHERE에 더할 조건 하나와 그 파라미터."""

    sql: sql.Composable
    params: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Statement:
    """실행할 SQL과 파라미터. explain은 이것을 값이 채워진 문자열로 바꿔 돌려준다."""

    sql: sql.Composed
    params: tuple[Any, ...] = ()


# ---------------------------------------------------------------------------
# 요청 읽기


def parse_list_query(obj: ObjectType, params: Mapping[str, str]) -> ListQuery:
    """쿼리 문자열(GET /objects/{type}?...)을 ListQuery로 바꾼다.

    문제는 모두 모아 한 번에 알린다.
    """
    violations: list[dict[str, Any]] = []
    filters = []
    for key, raw in params.items():
        if key in RESERVED:
            continue
        try:
            filters.append(_condition(obj, key, raw))
        except QueryError as e:
            violations.append(_violation(key, raw, e))
    try:
        sort = _sort(obj, params.get("sort"))
    except QueryError as e:
        violations.append(_violation("sort", params.get("sort"), e))
        sort = ()
    page = {}
    bounds = (("limit", DEFAULT_LIMIT, 1, MAX_LIMIT), ("offset", 0, 0, None))
    for name, default, low, high in bounds:
        try:
            page[name] = _int_param(params, name, default, low, high)
        except QueryError as e:
            violations.append(_violation(name, params.get(name), e))
    if violations:
        # reasonCode는 violations의 첫 번째 코드다 (docs/api.md 1절)
        message = "; ".join(v["detail"]["problem"] for v in violations)
        raise QueryError(violations[0]["code"], message, violations)
    return ListQuery(filters=tuple(filters), sort=sort, limit=page["limit"], offset=page["offset"])


def _violation(field: str, value: Any, e: QueryError) -> dict[str, Any]:
    # 키는 액션 엔진의 입력 검증(field, problem, value)과 같다
    return {"code": e.code, "detail": {"field": field, "problem": e.message, "value": value}}


def _condition(obj: ObjectType, key: str, raw: str) -> Condition:
    prop_name, sep, op = key.partition("__")
    prop = _filterable(obj, prop_name)
    if sep and op not in ("gte", "lte", "in"):
        # 같음은 속성=값으로만 쓴다 (docs/api.md 2절). __eq도 받지 않는다
        raise QueryError("INVALID_FILTER", f"연산자 '__{op}'는 없습니다 (__gte, __lte, __in)")
    op = op or "eq"
    if op in ("gte", "lte") and prop.type not in ORDERED_TYPES:
        raise QueryError("INVALID_FILTER", f"{prop_name}은 {prop.type}이라 __{op}를 쓸 수 없습니다")
    if op == "in":
        items = [v for v in raw.split(",") if v != ""]
        if not items:
            raise QueryError("INVALID_FILTER", f"{key}에 값이 없습니다")
        return Condition(prop_name, op, [coerce(prop, v) for v in items])
    return Condition(prop_name, op, coerce(prop, raw))


def _sort(obj: ObjectType, raw: str | None) -> tuple[SortKey, ...]:
    keys = []
    for part in (raw or "").split(","):
        if not part:
            continue
        descending = part.startswith("-")
        name = part.removeprefix("-")
        _filterable(obj, name)
        keys.append(SortKey(name, descending))
    return tuple(keys)


def _filterable(obj: ObjectType, name: str) -> Property | Derived:
    prop = obj.properties.get(name) or obj.derived.get(name)
    if prop is None:
        raise QueryError("INVALID_FILTER", f"{obj.name}에 속성 {name}이 없습니다")
    if prop.type == "json":
        raise QueryError("INVALID_FILTER", f"{name}은 json이라 거르거나 정렬할 수 없습니다")
    return prop


def coerce(prop: Property | Derived, raw: str) -> Any:
    """문자열 값을 속성 타입으로 바꾼다. 바꿀 수 없으면 INVALID_FILTER."""
    try:
        if prop.type == "int":
            return int(raw)
        if prop.type == "float":
            return float(raw)
        if prop.type == "bool":
            return {"true": True, "false": False}[raw]
        if prop.type == "datetime":
            value = datetime.fromisoformat(raw)
            if value.tzinfo is not None:
                raise ValueError("시간대 없이 씁니다")
            return value
    except (ValueError, KeyError):
        raise QueryError(
            "INVALID_FILTER", f"{prop.name}의 값 '{raw}'를 {prop.type}로 읽을 수 없습니다"
        ) from None
    if prop.type == "enum" and raw not in prop.values:
        raise QueryError(
            "INVALID_FILTER",
            f"{prop.name}의 값 '{raw}'는 {', '.join(prop.values)} 중 하나여야 합니다",
        )
    return raw


def _int_param(
    params: Mapping[str, str], name: str, default: int, low: int, high: int | None
) -> int:
    raw = params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value < low or (high is not None and value > high):
        bound = f"{low}~{high}" if high is not None else f"{low} 이상"
        raise QueryError("INVALID_INPUT", f"{name}은 {bound}의 정수여야 합니다 ('{raw}')")
    return value


# ---------------------------------------------------------------------------
# SQL 만들기

T = ROW_ALIAS  # 조회하는 객체 행의 별칭
ALIAS = sql.Identifier(T)


def select_by_id(obj: ObjectType, object_id: Any) -> Statement:
    query = sql.SQL("SELECT {columns} FROM {source} {alias} WHERE {key} = %s").format(
        columns=_columns(obj), source=_source(obj), alias=ALIAS, key=_column(obj, obj.primary_key)
    )
    return Statement(query, (object_id,))


def select_derived(obj: ObjectType, name: str, object_id: Any) -> Statement:
    """객체 하나의 파생 속성 하나만 계산한다. 식 평가기가 attr을 부를 때 쓴다."""
    query = sql.SQL("SELECT {expr} AS {name} FROM {source} {alias} WHERE {key} = %s").format(
        expr=_column(obj, name),
        name=sql.Identifier(name),
        source=_source(obj),
        alias=ALIAS,
        key=_column(obj, obj.primary_key),
    )
    return Statement(query, (object_id,))


def select_list(obj: ObjectType, q: ListQuery, scope: Clause | None = None) -> Statement:
    """scope는 필터보다 먼저 거는 조건이다. 링크를 따라갈 때 link_scope를 넣는다."""
    where, params = _where(obj, q.filters, scope)
    query = sql.SQL(
        "SELECT {columns} FROM {source} {alias}{where} ORDER BY {order} LIMIT %s OFFSET %s"
    ).format(
        columns=_columns(obj),
        source=_source(obj),
        alias=ALIAS,
        where=where,
        order=_order(obj, q.sort),
    )
    return Statement(query, (*params, q.limit, q.offset))


def count_list(obj: ObjectType, q: ListQuery, scope: Clause | None = None) -> Statement:
    where, params = _where(obj, q.filters, scope)
    query = sql.SQL("SELECT count(*) FROM {source} {alias}{where}").format(
        source=_source(obj), alias=ALIAS, where=where
    )
    return Statement(query, tuple(params))


def _source(obj: ObjectType) -> sql.Identifier:
    schema, table = obj.source.split(".")
    return sql.Identifier(schema, table)


def _column(obj: ObjectType, prop: str) -> sql.Composable:
    """속성 하나를 가리키는 SQL 식. 파생 속성이면 그 sql을 펼친 식이다."""
    if prop in obj.derived:
        return sql.SQL("({})").format(obj.derived[prop].expr)
    return sql.Identifier(T, obj.properties[prop].column)


def _columns(obj: ObjectType) -> sql.Composed:
    """저장 속성과 파생 속성을 모두 고르고 속성 이름으로 바꿔 부른다.

    이름은 겹치지 않는다 (spec._check_names). engine이 행을 properties와 derived로 나눈다.
    """
    return sql.SQL(", ").join(
        sql.SQL("{} AS {}").format(_column(obj, name), sql.Identifier(name))
        for name in (*obj.properties, *obj.derived)
    )


def with_clock(statement: Statement, now: datetime) -> Statement:
    """{now}가 읽는 가상 시계 시각을 문장 앞에 붙인다. 시각도 파라미터로 넘긴다.

    시간대 없는 datetime은 psycopg가 timestamp로 보내므로 따로 형을 적지 않는다.
    """
    query = sql.SQL("WITH clock(now) AS (SELECT %s) ") + statement.sql
    return Statement(query, (now, *statement.params))


def _where(
    obj: ObjectType, filters: tuple[Condition, ...], scope: Clause | None = None
) -> tuple[sql.Composable, list[Any]]:
    parts: list[sql.Composable] = []
    params: list[Any] = []
    if scope is not None:
        parts.append(scope.sql)
        params.extend(scope.params)
    if not filters and not parts:
        return sql.SQL(""), []
    for c in filters:
        column = _column(obj, c.prop)
        if c.op == "in":
            parts.append(sql.SQL("{} = ANY(%s)").format(column))
        else:
            parts.append(sql.SQL("{} {} %s").format(column, sql.SQL(OPERATORS[c.op])))
        params.append(c.value)
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def _order(obj: ObjectType, keys: tuple[SortKey, ...]) -> sql.Composed:
    """요청한 정렬 뒤에 primary key 오름차순을 붙인다. 같은 값끼리도 페이지가 흔들리지 않는다.

    null은 오름차순이면 뒤, 내림차순이면 앞에 온다 (Postgres 기본).
    """
    if obj.primary_key not in {k.prop for k in keys}:
        keys = (*keys, SortKey(obj.primary_key))
    return sql.SQL(", ").join(
        sql.SQL("{} {}").format(_column(obj, k.prop), sql.SQL("DESC" if k.descending else "ASC"))
        for k in keys
    )


# ---------------------------------------------------------------------------
# 링크

# 링크의 한쪽 끝을 무엇으로 채우나.
# 문자열이면 그 별칭의 행(컬럼), Mapping이면 객체 하나의 속성 값(파라미터)이다.
Binding = str | Mapping[str, Any]


def link_scope(link: Link, ends: Mapping[str, ObjectType], values: Mapping[str, Any]) -> Clause:
    """객체 하나(values)에서 link를 따라갈 때, 도착 객체 행(별칭 t)이 맞아야 하는 조건.

    ends는 links.yaml에 적힌 원래 방향의 {"from": 객체, "to": 객체}다. 역방향(inverse)이면
    values가 원래의 to이고 t가 원래의 from이다.

    pick이 있으면 "그 행이 pick으로 고른 행이다"를 조건으로 쓴다. ORDER BY ... LIMIT 1로 쓰면
    링크 API의 필터가 고르기 전에 걸려서 다른 행이 골라질 수 있다.
    """
    if link.inverse_of is None:
        parts, params = _match(link, ends, {"from": values, "to": T})
        if link.pick is not None:
            # 이 객체(원래 from)가 고른 행이 t여야 한다
            picked, picked_params = _picked(link, ends, values)
            to_key = sql.Identifier(T, ends["to"].key.column)
            parts.append(sql.SQL("{} = {}").format(to_key, picked))
            params += picked_params
    else:
        parts, params = _match(link, ends, {"from": T, "to": values})
        if link.pick is not None:
            # t(원래 from)가 고른 행이 이 객체(원래 to)여야 한다
            picked, picked_params = _picked(link, ends, T)
            parts.append(sql.SQL("%s = {}").format(picked))
            params += [values.get(ends["to"].primary_key), *picked_params]
    return Clause(sql.SQL("({})").format(sql.SQL(" AND ").join(parts)), tuple(params))


def _picked(
    link: Link, ends: Mapping[str, ObjectType], source: Binding
) -> tuple[sql.Composable, list[Any]]:
    """source(원래 from)에서 pick 규칙으로 고른 to의 primary key를 돌려주는 하위 질의."""
    assert link.pick is not None
    to, candidate = ends["to"], "p"
    where, params = _match(link, ends, {"from": source, "to": candidate})
    pk = sql.Identifier(candidate, to.key.column)
    query = sql.SQL(
        "(SELECT {pk} FROM {source} {alias} WHERE {where} ORDER BY {pick} {dir}, {pk} LIMIT 1)"
    ).format(
        pk=pk,
        source=_source(to),
        alias=sql.Identifier(candidate),
        where=sql.SQL(" AND ").join(where),
        pick=sql.Identifier(candidate, to.properties[link.pick.prop].column),
        dir=sql.SQL("DESC" if link.pick.descending else "ASC"),
    )
    return query, params


def _match(
    link: Link, ends: Mapping[str, ObjectType], bind: Mapping[str, Binding]
) -> tuple[list[sql.Composable], list[Any]]:
    """links.yaml의 join이나 through를 SQL 조건 목록으로. 파라미터는 나오는 순서대로 담는다."""
    params: list[Any] = []

    def ref(side: Side) -> sql.Composable:
        target = bind[side.end]
        column = ends[side.end].properties[side.prop].column
        if isinstance(target, str):
            return sql.Identifier(target, column)
        params.append(target.get(side.prop))
        return sql.Placeholder()

    if link.through is None:
        parts = [
            sql.SQL("{} {} {}").format(ref(c.left), sql.SQL(c.op), ref(c.right)) for c in link.join
        ]
        return parts, params

    via = "via"
    pairs = [
        sql.SQL("{} = {}").format(sql.Identifier(via, column), ref(Side(end, prop)))
        for end in ("from", "to")
        for prop, column in link.through[end].items()
    ]
    schema, table = link.through["source"].split(".")
    exists = sql.SQL("EXISTS (SELECT 1 FROM {source} {alias} WHERE {where})").format(
        source=sql.Identifier(schema, table),
        alias=sql.Identifier(via),
        where=sql.SQL(" AND ").join(pairs),
    )
    return [exists], params
