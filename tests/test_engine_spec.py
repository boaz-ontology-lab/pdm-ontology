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
