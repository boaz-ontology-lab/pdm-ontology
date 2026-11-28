"""링크 따라가기와 Resolver를 data/load.py로 적재한 Postgres에 붙여 본다. DB가 없으면 건너뛴다."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from app.action.expr import ExprError, compile_expr
from app.engine import spec
from app.engine.engine import QueryEngine
from app.engine.query import QueryError
from app.engine.resolver import EngineResolver

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2016, 1, 1, 6, 0)
# 결정 14의 데모 고장. 짝인 교체 기록을 숨겨 미해결이다
DEMO_FAILURES = {121: (17, "comp2"), 648: (88, "comp2"), 649: (88, "comp4")}


@pytest.fixture(scope="module")
def engine(pg):
    return QueryEngine(spec.load(), pg)


@pytest.fixture(scope="module")
def resolver(engine):
    return EngineResolver(engine)


@pytest.fixture
def ops(pg):
    """ops에 행을 넣어 보고 끝나면 되돌린다. 작업지시와 액션 교체 기록은 처음에 0건이다."""
    with pg.transaction(force_rollback=True):
        yield pg


def _insert(pg, sql: str, params: tuple) -> int:
    with pg.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _work_order(pg, machine_id, comp, status="OPEN", failure_id=None, assignee="mech1") -> int:
    return _insert(
        pg,
        "INSERT INTO ops.work_orders (machine_id, comp, type, status, assignee_id, created_by,"
        " created_at, completed_at, failure_id) VALUES (%s, %s, %s, %s, %s, 'mgr1', %s, %s, %s)"
        " RETURNING work_order_id",
        (
            machine_id,
            comp,
            "CORRECTIVE" if failure_id else "PREVENTIVE",
            status,
            assignee,
            NOW,
            NOW if status == "DONE" else None,
            failure_id,
        ),
    )


def _replacement(pg, work_order_id) -> int:
    return _insert(
        pg,
        "INSERT INTO ops.maintenance_log (datetime, machine_id, comp, work_order_id)"
        " SELECT %s, machine_id, comp, work_order_id FROM ops.work_orders"
        " WHERE work_order_id = %s RETURNING maint_id",
        (NOW, work_order_id),
    )


# ---------------------------------------------------------------------------
# resolvedBy (결정 4)


def _decision4_sql() -> str:
    """docs/decisions.md "핵심 규칙"의 resolvedBy SQL을 그대로 읽는다."""
    text = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    section = text.split("### resolvedBy", 1)[1]
    return re.search(r"```sql\n(.*?)```", section, re.S)[1]


def test_resolved_by_matches_decision_4(engine, resolver, pg):
    with pg.cursor() as cur:
        cur.execute(_decision4_sql())
        expected = dict(cur.fetchall())
    failures = engine.list("FailureEvent", {"limit": "500"}).total
    assert failures == 761

    actual = {}
    for offset in (0, 500):
        for f in engine.list("FailureEvent", {"limit": "500", "offset": str(offset)}).items:
            m = resolver.attr(f, "resolvedBy")
            if m is not None:
                actual[f.id] = m.id
    assert actual == expected
    assert len(actual) == 761 - len(DEMO_FAILURES)
    assert set(range(1, 762)) - set(actual) == set(DEMO_FAILURES)


def test_resolved_by_boundary_failure(engine, resolver):
    """2015-01-02 03:00에 몰린 18건은 같은 시각의 교체 기록이 없고 나중 교체 기록으로 이어진다."""
    page = engine.list("FailureEvent", {"datetime": "2015-01-02T03:00:00", "limit": "500"})
    assert page.total == 18
    for f in page.items:
        m = resolver.attr(f, "resolvedBy")
        assert m.properties["datetime"] > f.properties["datetime"]
        assert (m.properties["machineId"], m.properties["comp"]) == (
            f.properties["machineId"],
            f.properties["comp"],
        )


def test_resolves_is_the_inverse_of_resolved_by(engine, resolver):
    """교체 기록 하나가 여러 고장의 resolvedBy일 수 있다.

    고르지 않은 기록은 아무것도 고치지 않는다.
    """
    for machine_id in (17, 88, 1):
        failures = resolver.attr(resolver.get("Machine", machine_id), "hasFailures")
        expected: dict[int, list[int]] = {}
        for f in failures:
            m = resolver.attr(f, "resolvedBy")
            if m is not None:
                expected.setdefault(m.id, []).append(f.id)
        for m in resolver.attr(resolver.get("Machine", machine_id), "hasMaintenance"):
            assert [f.id for f in resolver.attr(m, "resolves")] == expected.get(m.id, [])


def test_action_replacement_resolves_demo_failure(engine, resolver, ops):
    failure = resolver.get("FailureEvent", 121)
    assert resolver.attr(failure, "resolvedBy") is None
    wo = _work_order(ops, 17, "comp2", status="DONE", failure_id=121)
    maint_id = _replacement(ops, wo)

    m = resolver.attr(failure, "resolvedBy")
    assert (m.id, m.properties["source"], m.properties["workOrderId"]) == (maint_id, "ACTION", wo)
    assert [f.id for f in resolver.attr(m, "resolves")] == [121]
    # 1:0..1과 그 역방향
    assert resolver.attr(m, "completes").id == wo
    assert resolver.attr(resolver.get("WorkOrder", wo), "completedAs").id == maint_id
    # 88의 데모 고장은 그대로 미해결이다
    assert resolver.attr(resolver.get("FailureEvent", 648), "resolvedBy") is None


# ---------------------------------------------------------------------------
# 방향과 개수


def test_one_to_many_is_a_list_in_primary_key_order(engine, resolver):
    m17 = resolver.get("Machine", 17)
    failures = resolver.attr(m17, "hasFailures")
    ids = [f.id for f in failures]
    assert ids == sorted(ids) and len(ids) == engine.list("FailureEvent", {"machineId": "17"}).total
    errors = resolver.attr(m17, "hasErrors")
    assert [e.id for e in errors] == sorted(e.id for e in errors)  # 에러 번호는 시간 순서가 아니다
    assert resolver.attr(resolver.get("Machine", 49), "hasWorkOrders") == []


def test_many_to_one_and_inverse(resolver):
    f = resolver.get("FailureEvent", 121)
    assert resolver.attr(f, "failedOn").id == 17  # hasFailures의 역방향
    assert resolver.attr(f, "failedComponent").id == "comp2"
    vendor = resolver.attr(resolver.get("Machine", 17), "suppliedBy")
    assert vendor.id == resolver.get("Machine", 17).properties["vendorId"]
    supplies = resolver.attr(vendor, "supplies")
    assert len(supplies) == {1: 33, 2: 67}[vendor.id]  # 결정 5


def test_zero_or_one_is_null_when_missing(resolver):
    assert resolver.attr(resolver.get("User", "venA"), "belongsTo").id == 1
    assert resolver.attr(resolver.get("User", "mgr1"), "belongsTo") is None


def test_through_link(resolver, pg):
    with pg.cursor() as cur:
        cur.execute("SELECT error_type, comp FROM ref.error_component_map")
        mapping = dict(cur.fetchall())
    for e in resolver.attr(resolver.get("Machine", 17), "hasErrors"):
        assert resolver.attr(e, "indicates").id == mapping[e.properties["errorType"]]


def test_work_order_links(resolver, ops):
    wo1 = _work_order(ops, 17, "comp2", failure_id=121)
    wo2 = _work_order(ops, 17, "comp4")
    w = resolver.get("WorkOrder", wo1)
    assert resolver.attr(w, "targets").id == 17
    assert resolver.attr(w, "addresses").id == 121
    assert resolver.attr(w, "assignedTo").id == "mech1"
    assert resolver.attr(w, "issuedBy").id == "mgr1"
    assert resolver.attr(w, "basedOn") is None  # inspectionRequestId가 null
    assert resolver.attr(resolver.get("WorkOrder", wo2), "addresses") is None
    assert [x.id for x in resolver.attr(resolver.get("Machine", 17), "hasWorkOrders")] == [wo1, wo2]
    assert [x.id for x in resolver.attr(resolver.get("FailureEvent", 121), "addressedBy")] == [wo1]


# ---------------------------------------------------------------------------
# 링크 API (GET /objects/{type}/{id}/links/{link})


def test_links_page(engine):
    page = engine.links("Machine", "17", "hasFailures", {"comp": "comp2", "limit": "2"})
    body = page.to_json()
    assert body["from"] == {"type": "Machine", "id": 17}
    assert (body["link"], body["to"]) == ("hasFailures", "FailureEvent")
    assert body["total"] == engine.list("FailureEvent", {"machineId": "17", "comp": "comp2"}).total
    assert len(body["items"]) == 2 and all(
        i["properties"]["comp"] == "comp2" for i in body["items"]
    )

    later = engine.links("Machine", "17", "hasFailures", {"comp": "comp2", "offset": "2"})
    assert later.total == page.total and later.items[0].id > page.items[-1].id


def test_links_page_for_single_link(engine):
    page = engine.links("FailureEvent", "121", "failedOn")
    assert (page.total, [i.id for i in page.items]) == (1, [17])
    assert engine.links("FailureEvent", "121", "resolvedBy").total == 0


def test_filter_does_not_change_pick(engine, resolver):
    """resolvedBy는 고른 뒤에 거른다. 고르기 전에 거르면 두 번째로 이른 교체 기록이 나온다."""
    f = resolver.get("FailureEvent", 108)
    picked = resolver.attr(f, "resolvedBy")
    at = picked.properties["datetime"]
    assert engine.links("FailureEvent", 108, "resolvedBy", {"datetime__gte": at.isoformat()}).total
    # 고른 기록 바로 뒤에도 같은 설비, 같은 부품의 교체 기록이 있다
    after = {"datetime__gte": at.replace(minute=1).isoformat()}
    same = {"machineId": str(f.properties["machineId"]), "comp": f.properties["comp"]}
    assert engine.list("Maintenance", {**same, **after}).total > 0
    assert engine.links("FailureEvent", 108, "resolvedBy", after).total == 0


def test_one_replacement_can_resolve_two_failures(resolver):
    """교체 510은 고장 두 건의 resolvedBy다. resolves는 둘 다 돌려준다."""
    resolved = resolver.attr(resolver.get("Maintenance", 510), "resolves")
    assert len(resolved) == 2
    assert all(resolver.attr(f, "resolvedBy").id == 510 for f in resolved)


def test_links_errors(engine):
    with pytest.raises(QueryError) as e:
        engine.links("Machine", "17", "hasAlarms")
    assert (e.value.code, e.value.status) == ("NOT_FOUND", 404)
    with pytest.raises(QueryError) as e:
        engine.links("Machine", "999", "hasFailures")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(QueryError) as e:
        engine.links(
            "Machine", "17", "hasFailures", {"errorType": "error1"}
        )  # 도착 객체의 속성으로 거른다
    assert e.value.code == "INVALID_FILTER"


def test_render_link_statement(engine):
    text = engine.render(engine.links("FailureEvent", "108", "resolvedBy").statement)
    assert 'ORDER BY "p"."datetime" ASC, "p"."maint_id" LIMIT 1' in text


# ---------------------------------------------------------------------------
# 식 평가기와 함께 (tests/test_action_expr.py의 식을 실제 엔진에서)


def _run(resolver, source, target, **inputs):
    names = {"target": target, "actor": None, "input": inputs, "now": NOW}
    return compile_expr(source).evaluate(resolver, names)


def test_expressions_on_real_engine(resolver, ops):
    m17, m49 = resolver.get("Machine", 17), resolver.get("Machine", 49)
    ids = [f.id for f in resolver.attr(m17, "hasFailures")]
    assert _run(resolver, "[f.failureId for f in target.hasFailures]", m17) == ids
    assert _run(
        resolver, "any(f.comp == input.comp for f in target.hasFailures)", m17, comp="comp2"
    )
    assert _run(resolver, "Machine(17).hasFailures", m49) == resolver.attr(m17, "hasFailures")
    assert _run(resolver, "Machine(999)", m17) is None
    assert _run(resolver, "Machine(999).hasWorkOrders", m17) is None
    assert _run(resolver, "[w for w in Machine(999).hasWorkOrders]", m17) == []
    assert _run(resolver, "max(w.workOrderId for w in target.hasWorkOrders)", m49) is None
    assert _run(resolver, "User(input.assigneeId).role == MECHANIC", m17, assigneeId="mech1")

    wo1 = _work_order(ops, 17, "comp2", failure_id=121)
    wo2 = _work_order(ops, 17, "comp1", status="DONE")
    w1 = resolver.get("WorkOrder", wo1)
    assert _run(resolver, "target.targets.machineId", w1) == 17
    assert _run(resolver, "max(w.workOrderId for w in target.hasWorkOrders)", m17) == wo2
    open_ids = "[w.workOrderId for w in target.hasWorkOrders if w.status == OPEN]"
    assert _run(resolver, open_ids, m17) == [wo1]
    duplicate = "none(w.status == OPEN and w.comp == input.comp for w in target.hasWorkOrders)"
    assert _run(resolver, duplicate, m17, comp="comp2") is False
    assert _run(resolver, duplicate, m17, comp="comp3") is True


@pytest.mark.parametrize(
    "source",
    [
        "target.nope",  # 없는 이름
        "Alarm(1)",  # 없는 객체 타입
        "Machine('abc')",  # id 타입이 틀림
        "User(1)",
        "target.status == DOWN",  # 파생 속성은 #17에서 계산한다
    ],
)
def test_expression_errors_on_real_engine(resolver, source):
    with pytest.raises(ExprError):
        _run(resolver, source, resolver.get("Machine", 17))


def test_single_link_with_many_rows_is_an_expression_error(pg):
    """N:1 링크에 여러 건이 걸리면 follow의 LookupError를 ExprError로 바꿔 올린다.

    지금 스펙에서는 일어나지 않으므로 hasFailures를 many_to_one으로 바꿔 만든다.
    """
    objects = spec._read(spec.DEFAULT_DIR / "objects.yaml")
    links = spec._read(spec.DEFAULT_DIR / "links.yaml")
    links["hasFailures"]["cardinality"] = "many_to_one"  # 설비 17은 고장이 여럿이다
    broken = EngineResolver(QueryEngine(spec.build(objects, links), pg))
    with pytest.raises(ExprError, match="many_to_one"):
        _run(broken, "target.hasFailures", broken.get("Machine", 17))
