"""ontology/objects.yaml과 links.yaml이 서로, 그리고 actions.yaml과 맞물리는지 검사한다.

표기 규칙은 check_conventions가 보고, 여기서는 구조와 참조를 본다.
SQL이 실제로 도는지, 값이 맞는지는 조회 엔진 테스트에서 DB를 붙여 본다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from check_conventions import check_links, check_objects
from test_actions_spec import _actions, _expressions

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = ROOT / "ontology"

TYPES = {"int", "float", "string", "bool", "datetime", "enum", "json"}
CARDINALITIES = {"one_to_many", "many_to_one", "many_to_zero_or_one", "one_to_zero_or_one"}
SINGLE = {"many_to_one", "many_to_zero_or_one", "one_to_zero_or_one"}
OBJECT_KEYS = {"description", "source", "primary_key", "properties", "derived", "timeseries"}
LINK_KEYS = {"description", "from", "to", "cardinality", "join", "through", "pick", "inverse"}
SOURCE = re.compile(r"^(raw|ref|ops|core)\.[a-z_]+$")
PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)(?:\.([A-Za-z][A-Za-z0-9]*))?\}")
ALIAS_DECL = re.compile(r"\{([A-Z][A-Za-z0-9]*)\}\s+([a-z][a-z0-9_]*)")
JOIN_EQ = re.compile(r"^(\w+) = (\w+)$")
JOIN_CMP = re.compile(r"^(from|to)\.(\w+) (=|<|<=|>|>=) (from|to)\.(\w+)$")
PICK = re.compile(r"^(earliest|latest)\(to\.(\w+)\)$")


def _load(name: str) -> dict[str, Any]:
    with (ONTOLOGY / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def objects() -> dict[str, Any]:
    return _load("objects.yaml")


@pytest.fixture(scope="module")
def links() -> dict[str, Any]:
    return _load("links.yaml")


@pytest.fixture(scope="module")
def actions() -> dict[str, Any]:
    return _load("actions.yaml")


def _props(obj: dict[str, Any]) -> dict[str, Any]:
    return obj.get("properties") or {}


def _derived(obj: dict[str, Any]) -> dict[str, Any]:
    return obj.get("derived") or {}


def _link_names(links: dict[str, Any]) -> dict[str, set[str]]:
    """객체 타입마다 그 객체에서 출발하는 링크 이름 (inverse 포함)."""
    names: dict[str, set[str]] = {}
    for name, link in links.items():
        names.setdefault(link["from"], set()).add(name)
        if "inverse" in link:
            names.setdefault(link["to"], set()).add(link["inverse"]["name"])
    return names


def test_conventions(objects, links):
    assert check_objects(objects) == []
    assert check_links(links) == []


def test_object_shape(objects):
    for name, obj in objects.items():
        assert set(obj) <= OBJECT_KEYS, name
        for key in ("description", "source", "primary_key", "properties"):
            assert obj.get(key), f"{name}: {key}가 없습니다"
        assert SOURCE.match(obj["source"]), f"{name}: source '{obj['source']}'"
        assert obj["primary_key"] in _props(obj), f"{name}: primary_key가 properties에 없습니다"


def test_properties(objects):
    for name, obj in objects.items():
        for prop, spec in _props(obj).items():
            where = f"{name}.{prop}"
            assert spec.get("column"), where
            assert spec.get("type") in TYPES, where
            assert spec.get("description"), where
            assert ("values" in spec) == (spec["type"] == "enum"), where
            if "ref" in spec:
                assert spec["ref"] in objects, f"{where}: ref {spec['ref']}가 없습니다"


def test_names_do_not_collide(objects, links):
    link_names = _link_names(links)
    for name, obj in objects.items():
        groups = [set(_props(obj)), set(_derived(obj)), set(obj.get("timeseries") or {})]
        groups.append(link_names.get(name, set()))
        seen: set[str] = set()
        for group in groups:
            assert not seen & group, f"{name}: 이름이 겹칩니다 {sorted(seen & group)}"
            seen |= group
    inverse = [link["inverse"]["name"] for link in links.values() if "inverse" in link]
    assert not set(inverse) & set(links), "inverse 이름이 링크 이름과 겹칩니다"


def test_links(objects, links):
    for name, link in links.items():
        assert set(link) <= LINK_KEYS, name
        assert link.get("description"), name
        assert link["cardinality"] in CARDINALITIES, name
        src, dst = objects.get(link["from"]), objects.get(link["to"])
        assert src and dst, f"{name}: from이나 to 객체가 없습니다"
        assert ("join" in link) != ("through" in link), f"{name}: join과 through 중 하나만"

        for cond in link.get("join") or []:
            if m := JOIN_EQ.match(cond):
                assert m[1] in _props(src), f"{name}: from 속성 {m[1]}"
                assert m[2] in _props(dst), f"{name}: to 속성 {m[2]}"
            elif m := JOIN_CMP.match(cond):
                for side, prop in ((m[1], m[2]), (m[4], m[5])):
                    assert prop in _props(src if side == "from" else dst), f"{name}: {side}.{prop}"
            else:
                pytest.fail(f"{name}: join 조건 '{cond}'을 읽을 수 없습니다")

        if through := link.get("through"):
            assert SOURCE.match(through["source"]), name
            assert set(through["from"]) <= set(_props(src)), name
            assert set(through["to"]) <= set(_props(dst)), name

        if pick := link.get("pick"):
            m = PICK.match(pick)
            assert m and m[2] in _props(dst), f"{name}: pick '{pick}'"
            assert link["cardinality"] in SINGLE, f"{name}: pick은 하나를 고르는 링크에만"

        if inverse := link.get("inverse"):
            assert inverse["cardinality"] in CARDINALITIES, name


def test_derived_shape(objects):
    for name, obj in objects.items():
        for prop, spec in _derived(obj).items():
            where = f"{name}.{prop}"
            assert spec.get("type") in TYPES, where
            assert spec.get("description"), where
            assert spec.get("sql"), where
            assert ("values" in spec) == (spec["type"] == "enum"), where


def test_derived_placeholders(objects):
    """sql의 자리표시자가 모두 있는 이름을 가리키는지 본다."""
    for name, obj in objects.items():
        own = set(_props(obj)) | set(_derived(obj)) | {"now"}
        for prop, spec in _derived(obj).items():
            sql = spec["sql"]
            where = f"{name}.{prop}"
            aliases = {alias: type_name for type_name, alias in ALIAS_DECL.findall(sql)}
            for type_name in aliases.values():
                assert type_name in objects, f"{where}: 객체 타입 {type_name}이 없습니다"
            for head, tail in PLACEHOLDER.findall(sql):
                if not tail and head in objects:
                    continue
                if not tail:
                    assert head in own, f"{where}: {{{head}}}가 {name}에 없습니다"
                    continue
                assert head in aliases, f"{where}: 별칭 {head}가 선언되지 않았습니다"
                alias_type = aliases[head]
                assert tail in _derived(objects[alias_type]), (
                    f"{where}: {alias_type}.{tail}은 파생 속성이 아닙니다"
                )


def test_derived_has_no_cycle(objects):
    def deps(type_name: str, prop: str) -> set[tuple[str, str]]:
        sql = objects[type_name]["derived"][prop]["sql"]
        aliases = {alias: t for t, alias in ALIAS_DECL.findall(sql)}
        out = set()
        for head, tail in PLACEHOLDER.findall(sql):
            if tail:
                out.add((aliases[head], tail))
            elif head in _derived(objects[type_name]):
                out.add((type_name, head))
        return out

    def visit(node: tuple[str, str], path: list[tuple[str, str]]) -> None:
        assert node not in path, f"파생 속성이 서로를 부릅니다: {path + [node]}"
        for nxt in deps(*node):
            visit(nxt, path + [node])

    for type_name, obj in objects.items():
        for prop in _derived(obj):
            visit((type_name, prop), [])


def test_actions_use_defined_names(objects, links, actions):
    """actions.yaml 식이 부르는 속성, 파생 속성, 링크가 이 스펙에 모두 있는지 본다."""
    known: set[str] = set()
    for obj in objects.values():
        known |= set(_props(obj)) | set(_derived(obj))
    for names in _link_names(links).values():
        known |= names
    for name, action in _actions(actions).items():
        for expr in _expressions(action):
            for node in ast.walk(ast.parse(expr.strip(), mode="eval")):
                if not isinstance(node, ast.Attribute):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id == "input":
                    continue
                assert node.attr in known, f"{name}: '{node.attr}'이 objects, links에 없습니다"


def test_action_targets_and_effects(objects, actions):
    for name, action in _actions(actions).items():
        assert action["target"] in objects, name
        for field, spec in action["input"].items():
            if "ref" in spec:
                assert spec["ref"] in objects, f"{name}.input.{field}"
        for effect in action["effect"]:
            if "create" in effect:
                created = objects[effect["create"]]
                missing = set(effect["values"]) - set(_props(created))
                assert not missing, f"{name}: {effect['create']}에 없는 속성 {sorted(missing)}"
            else:
                field = effect["set"]["field"]
                stored = {p for obj in objects.values() for p in _props(obj)}
                assert field in stored, f"{name}: set은 저장 속성만 바꿀 수 있습니다 ({field})"
