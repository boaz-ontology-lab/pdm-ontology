"""조회 엔진의 스펙 로더. 실제 스펙을 읽고, 틀린 스펙이면 SpecError로 실패하는지 본다."""

from __future__ import annotations

import copy

import pytest

from app.engine import spec
from app.engine.spec import SpecError


@pytest.fixture(scope="module")
def docs():
    return spec._read(spec.DEFAULT_DIR / "objects.yaml"), spec._read(
        spec.DEFAULT_DIR / "links.yaml"
    )


def _problems(objects_doc, links_doc) -> list[str]:
    with pytest.raises(SpecError) as e:
        spec.build(objects_doc, links_doc)
    return e.value.problems


def test_load_real_spec():
    o = spec.load()
    assert len(o.objects) == 9
    machine = o.object("Machine")
    assert machine.source == "core.machine"
    assert machine.key.column == "machine_id"
    assert machine.write and machine.write.table == "ops.machine_state"
    assert "status" in machine.derived
    assert o.object("Nope") is None


def test_links_include_inverse():
    o = spec.load()
    has_failures = o.link("Machine", "hasFailures")
    assert (has_failures.from_type, has_failures.to_type) == ("Machine", "FailureEvent")
    failed_on = o.link("FailureEvent", "failedOn")
    assert failed_on.inverse_of == "hasFailures"
    assert (failed_on.from_type, failed_on.to_type, failed_on.cardinality) == (
        "FailureEvent",
        "Machine",
        "many_to_one",
    )
    # actions.yaml이 부르는 역방향 링크
    assert o.link("Machine", "hasWorkOrders") and o.link("FailureEvent", "addressedBy")


def test_link_to_unknown_object(docs):
    objects_doc, links_doc = copy.deepcopy(docs)
    links_doc["hasErrors"]["to"] = "Alarm"
    assert any("hasErrors" in p and "Alarm" in p for p in _problems(objects_doc, links_doc))


def test_name_collision_between_property_and_link(docs):
    objects_doc, links_doc = copy.deepcopy(docs)
    links_doc["hasErrors"]["inverse"]["name"] = "comp"  # ErrorEvent에는 comp가 없다
    objects_doc["ErrorEvent"]["properties"]["comp"] = {
        "column": "comp",
        "type": "string",
        "description": "x",
    }
    assert any("ErrorEvent" in p and "'comp'" in p for p in _problems(objects_doc, links_doc))


def test_duplicate_link_name(docs):
    objects_doc, links_doc = copy.deepcopy(docs)
    # 출발 객체가 다르면 같은 이름이어도 괜찮다 (ErrorEvent.occurredOn, Maintenance.occurredOn)
    links_doc["hasMaintenance"]["inverse"]["name"] = "occurredOn"
    spec.build(objects_doc, links_doc)
    # 둘 다 Maintenance에서 출발하는 역방향이다 (resolvedBy의 역방향이 resolves)
    links_doc["hasMaintenance"]["inverse"]["name"] = "resolves"
    assert any("resolves" in p and "두 번" in p for p in _problems(objects_doc, links_doc))


def test_problems_are_collected(docs):
    objects_doc, links_doc = copy.deepcopy(docs)
    objects_doc["Machine"]["primary_key"] = "id"
    objects_doc["Machine"]["write"]["table"] = "raw.machines"
    objects_doc["Machine"]["properties"]["vendorId"]["ref"] = "Supplier"
    problems = _problems(objects_doc, links_doc)
    assert len(problems) == 3, problems


def test_join_and_pick_are_parsed():
    o = spec.load()
    resolved_by = o.link("FailureEvent", "resolvedBy")
    assert resolved_by.join[2] == spec.JoinCondition(
        spec.Side("to", "datetime"), ">=", spec.Side("from", "datetime")
    )
    assert resolved_by.pick == spec.Pick("datetime", descending=False)
    assert not resolved_by.many
    # 역방향도 같은 join과 pick을 가진다
    resolves = o.link("Maintenance", "resolves")
    assert (resolves.join, resolves.pick, resolves.many) == (
        resolved_by.join,
        resolved_by.pick,
        True,
    )
    assigned_to = o.link("WorkOrder", "assignedTo")
    assert assigned_to.join == (
        spec.JoinCondition(spec.Side("from", "assigneeId"), "=", spec.Side("to", "userId")),
    )


@pytest.mark.parametrize(
    "link, key, value, expected",
    [
        ("hasErrors", "join", ["machineId = machine"], "ErrorEvent에 속성 machine"),
        ("hasErrors", "join", ["machineId == machineId"], "읽을 수 없습니다"),
        ("hasErrors", "join", ["from.machineId = from.age"], "하나씩"),
        ("resolvedBy", "pick", "first(to.datetime)", "pick"),
        ("resolvedBy", "pick", "earliest(to.when)", "when"),
        (
            "indicates",
            "through",
            {"source": "ref.error_component_map", "from": {"code": "x"}, "to": {"comp": "comp"}},
            "code",
        ),
    ],
)
def test_bad_join_or_pick(docs, link, key, value, expected):
    objects_doc, links_doc = copy.deepcopy(docs)
    links_doc[link][key] = value
    assert any(expected in p for p in _problems(objects_doc, links_doc))
