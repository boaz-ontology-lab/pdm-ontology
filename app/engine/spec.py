"""ontology/objects.yaml, links.yaml을 읽어 dataclass로 바꾸고 검증한다.

서버가 뜰 때 한 번 읽는다. 스펙이 틀리면 문제를 모두 모아 SpecError 하나로 실패한다.
CI의 tests/test_ontology_spec.py가 더 자세히 검사하고,
여기서는 엔진이 돌기 위해 꼭 맞아야 하는 것만 본다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "ontology"

TYPES = frozenset({"int", "float", "string", "bool", "datetime", "enum", "json"})
CARDINALITIES = frozenset(
    {"one_to_many", "many_to_one", "many_to_zero_or_one", "one_to_zero_or_one"}
)
SOURCE = re.compile(r"^(raw|ref|ops|core)\.[a-z_]+$")
# links.yaml의 join 한 줄: "machineId = machineId", "to.datetime >= from.datetime"
_SIDE = r"(?:(from|to)\.)?([A-Za-z][A-Za-z0-9]*)"
JOIN = re.compile(rf"^\s*{_SIDE}\s*(=|<=|>=|<|>)\s*{_SIDE}\s*$")
PICK = re.compile(r"^(earliest|latest)\(to\.([A-Za-z][A-Za-z0-9]*)\)$")


class SpecError(Exception):
    """스펙이 엔진이 돌 수 없는 모양이다. problems에 문제를 모두 담는다."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("ontology 스펙 오류:\n" + "\n".join(f"- {p}" for p in problems))
        self.problems = problems


@dataclass(frozen=True)
class Property:
    name: str
    column: str
    type: str
    description: str
    values: tuple[str, ...] = ()
    ref: str | None = None


@dataclass(frozen=True)
class Derived:
    name: str
    type: str
    description: str
    sql: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Write:
    table: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ObjectType:
    name: str
    description: str
    source: str
    primary_key: str
    properties: Mapping[str, Property]
    derived: Mapping[str, Derived] = field(default_factory=dict)
    write: Write | None = None
    timeseries: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Property:
        return self.properties[self.primary_key]


@dataclass(frozen=True)
class Side:
    """join 조건의 한쪽. end는 links.yaml에 적힌 원래 방향의 from이나 to다."""

    end: str  # from | to
    prop: str


@dataclass(frozen=True)
class JoinCondition:
    left: Side
    op: str  # = < <= > >=
    right: Side


@dataclass(frozen=True)
class Pick:
    """to가 여럿 맞을 때 하나를 고르는 규칙. 같은 값끼리는 to의 primary key가 작은 것이다."""

    prop: str  # to 객체의 속성
    descending: bool  # latest면 True


@dataclass(frozen=True)
class Link:
    """links.yaml의 링크 하나. 역방향(inverse)도 같은 모양의 Link로 따로 둔다.

    역방향도 join, through, pick은 원래 방향 그대로 담는다. from_type, to_type만 바뀐다.
    """

    name: str
    from_type: str
    to_type: str
    cardinality: str
    description: str
    join: tuple[JoinCondition, ...] = ()
    through: Mapping[str, Any] | None = None
    pick: Pick | None = None
    inverse_of: str | None = None  # 역방향이면 원래 링크 이름

    @property
    def many(self) -> bool:
        """따라가면 목록인가. 아니면 객체 하나나 null이다."""
        return self.cardinality == "one_to_many"


@dataclass(frozen=True)
class Ontology:
    objects: Mapping[str, ObjectType]
    links: Mapping[str, Mapping[str, Link]]  # 출발 객체 타입마다 {링크 이름: 링크}

    def object(self, type_name: str) -> ObjectType | None:
        return self.objects.get(type_name)

    def link(self, type_name: str, link_name: str) -> Link | None:
        return self.links.get(type_name, {}).get(link_name)


def load(directory: Path = DEFAULT_DIR) -> Ontology:
    return build(_read(directory / "objects.yaml"), _read(directory / "links.yaml"))


def build(objects_doc: Mapping[str, Any], links_doc: Mapping[str, Any]) -> Ontology:
    """YAML을 읽은 dict에서 Ontology를 만든다. 테스트가 틀린 스펙을 넣어 볼 수 있게 따로 둔다."""
    problems: list[str] = []
    objects = {name: _object(name, spec, problems) for name, spec in (objects_doc or {}).items()}
    links: dict[str, dict[str, Link]] = {name: {} for name in objects}
    for name, spec in (links_doc or {}).items():
        for link in _links(name, spec, objects, problems):
            if link.name in links[link.from_type]:
                problems.append(f"{link.from_type}: 링크 이름 '{link.name}'이 두 번 나옵니다")
            links[link.from_type][link.name] = link
    _check_refs(objects, problems)
    _check_names(objects, links, problems)
    if problems:
        raise SpecError(problems)
    return Ontology(objects=objects, links=links)


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _object(name: str, spec: Mapping[str, Any], problems: list[str]) -> ObjectType:
    source = spec.get("source", "")
    if not SOURCE.match(source):
        problems.append(f"{name}: source '{source}'가 schema.table 모양이 아닙니다")

    properties: dict[str, Property] = {}
    for prop, p in (spec.get("properties") or {}).items():
        if p.get("type") not in TYPES or not p.get("column"):
            problems.append(f"{name}.{prop}: type이나 column이 틀렸습니다")
        properties[prop] = Property(
            name=prop,
            column=p.get("column", ""),
            type=p.get("type", ""),
            description=p.get("description", ""),
            values=tuple(p.get("values") or ()),
            ref=p.get("ref"),
        )

    derived: dict[str, Derived] = {}
    for prop, d in (spec.get("derived") or {}).items():
        if d.get("type") not in TYPES or not d.get("sql"):
            problems.append(f"{name}.{prop}: 파생 속성의 type이나 sql이 틀렸습니다")
        derived[prop] = Derived(
            name=prop,
            type=d.get("type", ""),
            description=d.get("description", ""),
            sql=d.get("sql", ""),
            values=tuple(d.get("values") or ()),
        )

    primary_key = spec.get("primary_key", "")
    if primary_key not in properties:
        problems.append(f"{name}: primary_key '{primary_key}'가 properties에 없습니다")

    write = None
    if w := spec.get("write"):
        write = Write(table=w.get("table", ""), fields=tuple(w.get("fields") or ()))
        if not write.table.startswith("ops."):
            problems.append(f"{name}: write.table '{write.table}'은 ops 테이블이어야 합니다")
        if missing := set(write.fields) - set(properties):
            problems.append(f"{name}: write.fields에 없는 속성 {sorted(missing)}")

    return ObjectType(
        name=name,
        description=spec.get("description", ""),
        source=source,
        primary_key=primary_key,
        properties=properties,
        derived=derived,
        write=write,
        timeseries=spec.get("timeseries") or {},
    )


def _links(
    name: str, spec: Mapping[str, Any], objects: Mapping[str, ObjectType], problems: list[str]
) -> list[Link]:
    src, dst = spec.get("from"), spec.get("to")
    bad = [end for end, t in (("from", src), ("to", dst)) if t not in objects]
    if bad:
        problems.append(f"링크 {name}: {', '.join(bad)} 객체 타입이 없습니다 ({src} → {dst})")
        return []
    if spec.get("cardinality") not in CARDINALITIES:
        problems.append(f"링크 {name}: cardinality '{spec.get('cardinality')}'")
    if ("join" in spec) == ("through" in spec):
        problems.append(f"링크 {name}: join과 through 중 하나만 써야 합니다")

    ends = {"from": objects[src], "to": objects[dst]}
    link = Link(
        name=name,
        from_type=src,
        to_type=dst,
        cardinality=spec.get("cardinality", ""),
        description=spec.get("description", ""),
        join=tuple(_join(name, j, ends, problems) for j in spec.get("join") or ()),
        through=_through(name, spec.get("through"), ends, problems),
        pick=_pick(name, spec.get("pick"), ends["to"], problems),
    )
    out = [link]
    if inverse := spec.get("inverse"):
        if inverse.get("cardinality") not in CARDINALITIES:
            problems.append(f"링크 {name}: inverse cardinality '{inverse.get('cardinality')}'")
        out.append(
            Link(
                name=inverse.get("name", ""),
                from_type=dst,
                to_type=src,
                cardinality=inverse.get("cardinality", ""),
                description=link.description,
                join=link.join,
                through=link.through,
                pick=link.pick,  # resolves는 resolvedBy가 고른 교체 기록에서만 이어진다
                inverse_of=name,
            )
        )
    return out


def _join(
    name: str, text: str, ends: Mapping[str, ObjectType], problems: list[str]
) -> JoinCondition:
    """ "a = b"는 from.a = to.b다. 앞에 from., to.을 붙이면 그쪽으로 읽는다."""
    m = JOIN.match(str(text))
    if not m:
        problems.append(f"링크 {name}: join '{text}'을 읽을 수 없습니다")
        return JoinCondition(Side("from", ""), "=", Side("to", ""))
    left = Side(m[1] or "from", m[2])
    right = Side(m[4] or "to", m[5])
    if left.end == right.end:
        problems.append(f"링크 {name}: join '{text}'은 from과 to를 하나씩 비교해야 합니다")
    for side in (left, right):
        _need_property(name, ends[side.end], side.prop, problems)
    return JoinCondition(left, m[3], right)


def _through(
    name: str, spec: Mapping[str, Any] | None, ends: Mapping[str, ObjectType], problems: list[str]
) -> Mapping[str, Any] | None:
    if spec is None:
        return None
    if not SOURCE.match(str(spec.get("source", ""))):
        problems.append(f"링크 {name}: through.source '{spec.get('source')}'")
    for end in ("from", "to"):
        mapping = spec.get(end) or {}
        if not mapping:
            problems.append(f"링크 {name}: through.{end}가 비어 있습니다")
        for prop in mapping:
            _need_property(name, ends[end], prop, problems)
    return spec


def _pick(name: str, text: str | None, to: ObjectType, problems: list[str]) -> Pick | None:
    if text is None:
        return None
    m = PICK.match(str(text))
    if not m:
        problems.append(f"링크 {name}: pick '{text}'은 earliest(to.속성)이나 latest(to.속성)입니다")
        return None
    _need_property(name, to, m[2], problems)
    return Pick(prop=m[2], descending=m[1] == "latest")


def _need_property(name: str, obj: ObjectType, prop: str, problems: list[str]) -> None:
    if prop and prop not in obj.properties:
        problems.append(f"링크 {name}: {obj.name}에 속성 {prop}이 없습니다")


def _check_refs(objects: Mapping[str, ObjectType], problems: list[str]) -> None:
    for obj in objects.values():
        for prop in obj.properties.values():
            if prop.ref and prop.ref not in objects:
                problems.append(f"{obj.name}.{prop.name}: ref {prop.ref} 객체 타입이 없습니다")


def _check_names(
    objects: Mapping[str, ObjectType], links: Mapping[str, Mapping[str, Link]], problems: list[str]
) -> None:
    """한 객체 안에서 속성, 파생 속성, 시계열, 링크 이름이 겹치면 x.이름이 무엇인지 모호하다."""
    for obj in objects.values():
        seen: dict[str, str] = {}
        groups = (
            ("속성", obj.properties),
            ("파생 속성", obj.derived),
            ("시계열", obj.timeseries),
            ("링크", links.get(obj.name, {})),
        )
        for kind, names in groups:
            for n in names:
                if n in seen:
                    problems.append(f"{obj.name}: '{n}'이 {seen[n]}과 {kind}에 모두 있습니다")
                seen.setdefault(n, kind)
