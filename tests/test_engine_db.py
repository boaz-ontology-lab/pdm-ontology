"""조회 엔진을 data/load.py로 적재한 Postgres에 붙여 본다. DB가 없으면 건너뛴다 (conftest.pg)."""

from __future__ import annotations

import pytest

from app.engine import spec
from app.engine.engine import QueryEngine
from app.engine.query import QueryError


@pytest.fixture(scope="module")
def engine(pg):
    return QueryEngine(spec.load(), pg)


# 원본 건수는 docs/synthetic-data.md. Maintenance는 숨긴 3건을 뺀 수 (결정 14). ops는 처음에 0건이다
COUNTS = {
    "Machine": 100,
    "ErrorEvent": 3919,
    "FailureEvent": 761,
    "Maintenance": 3283,
    "Component": 4,
    "Vendor": 2,
    "User": 8,
    "WorkOrder": 0,
    "InspectionRequest": 0,
}


@pytest.mark.parametrize("type_name, total", COUNTS.items())
def test_every_object_type_runs(engine, type_name, total):
    """objects.yaml의 source와 column이 실제 DB와 맞아야 이 SELECT가 돈다."""
    page = engine.list(type_name, {"limit": "1"})
    assert page.total == total
    if total:
        item = page.items[0]
        obj = engine.ontology.object(type_name)
        assert set(item.properties) == set(obj.properties)
        assert engine.get(type_name, item.id) == item


def test_machine_filters(engine):
    assert engine.list("Machine", {"model": "model3"}).total == 35
    assert (
        engine.list("Machine", {"model__in": "model1,model2"}).total
        == 100 - 35 - engine.list("Machine", {"model": "model4"}).total
    )
    old = engine.list("Machine", {"age__gte": "15", "limit": "500"})
    assert old.total == len(old.items) and all(m.properties["age"] >= 15 for m in old.items)


def test_in_filter_on_int_and_datetime(engine):
    """__in은 = ANY(배열)로 넘긴다. 문자열이 아닌 컬럼에서도 배열 타입이 맞아야 한다."""
    page = engine.list("Machine", {"machineId__in": "88,17,3"})
    assert [m.id for m in page.items] == [3, 17, 88]
    page = engine.list(
        "FailureEvent",
        {"machineId": "88", "datetime__in": "2015-11-30T06:00:00,2015-12-30T06:00:00"},
    )
    assert {f.properties["comp"] for f in page.items} >= {"comp2", "comp4"}  # 결정 14의 648, 649
    assert engine.list("User", {"vendorId__in": "1,2"}).total == 2  # VENDOR 역할만 vendorId가 있다


def test_sort_and_paging(engine):
    by_age = engine.list("Machine", {"sort": "-age", "limit": "500"}).items
    ages = [m.properties["age"] for m in by_age]
    assert ages == sorted(ages, reverse=True)
    # 같은 나이끼리는 machineId 오름차순이라 페이지를 나눠도 이어 붙이면 같다
    pages = [
        engine.list("Machine", {"sort": "-age", "limit": "30", "offset": str(o)})
        for o in range(0, 100, 30)
    ]
    assert [m.id for p in pages for m in p.items] == [m.id for m in by_age]
    assert all(p.total == 100 for p in pages)
    assert engine.list("Machine", {"offset": "100"}).items == ()


def test_get(engine):
    m17 = engine.get("Machine", "17")
    assert m17.to_json() == {
        "type": "Machine",
        "id": 17,
        "properties": {
            "machineId": 17,
            "model": m17.properties["model"],
            "age": m17.properties["age"],
            "vendorId": m17.properties["vendorId"],
            "lastRestartAt": None,
            "lastRestartBy": None,
            "lastRestartWorkOrderId": None,
        },
        "derived": {},
        "masked": [],
    }
    assert engine.get("Machine", 101) is None
    assert engine.get("User", "mgr1").properties["role"] == "MANAGER"
    assert engine.get("Component", "comp2").id == "comp2"


def test_datetime_filter_and_json(engine):
    page = engine.list(
        "FailureEvent",
        {"machineId": "88", "datetime__gte": "2015-12-01T00:00:00", "sort": "datetime"},
    )
    assert page.total >= 1
    assert all(f.to_json()["properties"]["datetime"] >= "2015-12-01" for f in page.items)


def test_injection_is_just_a_value(engine):
    assert engine.list("Machine", {"model": "model1' OR '1'='1"}).total == 0


def test_errors(engine):
    with pytest.raises(QueryError) as e:
        engine.list("Alarm")
    assert (e.value.code, e.value.status) == ("NOT_FOUND", 404)
    with pytest.raises(QueryError) as e:
        engine.get("Machine", "abc")
    assert e.value.code == "INVALID_INPUT"
    with pytest.raises(QueryError) as e:
        engine.list("Machine", {"riskLevel": "HIGH"})
    assert e.value.code == "INVALID_FILTER"


def test_render_for_explain(engine):
    page = engine.list("Machine", {"model__in": "model1,model2", "limit": "3"})
    text = engine.render(page.statement)
    assert "ANY('{model1,model2}')" in text and text.endswith("LIMIT 3 OFFSET 0")
