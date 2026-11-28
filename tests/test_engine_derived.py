"""파생 속성을 data/load.py로 적재한 Postgres에 붙여 본다. DB가 없으면 건너뛴다 (conftest.pg).

기대값은 docs/decisions.md "핵심 규칙"과 결정 14의 데모 설비다.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.action.expr import ExprError, compile_expr, compile_template
from app.engine import spec
from app.engine.engine import BASE_TIME, ObjectRecord, QueryEngine
from app.engine.query import QueryError
from app.engine.resolver import EngineResolver

ROOT = Path(__file__).resolve().parents[1]
NOW = BASE_TIME


@pytest.fixture(scope="module")
def engine(pg):
    return QueryEngine(spec.load(), pg)


@pytest.fixture(scope="module")
def resolver(engine):
    return EngineResolver(engine)


@pytest.fixture
def ops(pg):
    """ops에 행을 넣어 보고 끝나면 되돌린다."""
    with pg.transaction(force_rollback=True):
        yield pg


def _machines(engine):
    return engine.list("Machine", {"limit": "500"}).items


# ---------------------------------------------------------------------------
# 기준 시각의 값


def test_risk_level_counts(engine):
    """결정 9. #22에서 숨긴 교체 기록을 반영한 값이다."""
    counts = Counter(m.derived["riskLevel"] for m in _machines(engine))
    assert counts == {"HIGH": 5, "MEDIUM": 45, "LOW": 50}


def test_risk_reasons_explain_risk_level(engine):
    for m in _machines(engine):
        rules = {r["rule"] for r in m.derived["riskReasons"]}
        if "RECENT_ERROR_NOT_REPLACED" in rules:
            assert m.derived["riskLevel"] == "HIGH"
        elif "REPLACED_OVER_90_DAYS" in rules:
            assert m.derived["riskLevel"] == "MEDIUM"
        else:
            assert (m.derived["riskLevel"], m.derived["riskReasons"]) == ("LOW", [])


def test_demo_machines(engine, resolver):
    """결정 14: 설비 17은 DOWN, 설비 88은 미해결 고장 2건. 나머지는 RUNNING이다."""
    statuses = {m.id: m.derived["status"] for m in _machines(engine)}
    assert statuses.pop(17) == "DOWN" and statuses.pop(88) == "DOWN"
    assert set(statuses.values()) == {"RUNNING"}

    m88 = resolver.get("Machine", 88)
    names = {"target": m88, "actor": None, "input": {}, "now": NOW}
    template = compile_template(
        "미해결 고장 {count(f for f in target.hasFailures if not f.isResolved)}건"
    )
    assert template.render(resolver, names) == "미해결 고장 2건"
    assert m88.derived["hasOpenFailure"] and m88.derived["hasUncoveredFailure"]


def test_is_resolved_follows_resolved_by(engine, resolver):
    """결정 11: isResolved는 resolvedBy 링크가 있는지다."""
    for offset in (0, 500):
        for f in engine.list("FailureEvent", {"limit": "500", "offset": str(offset)}).items:
            assert f.derived["isResolved"] == (resolver.attr(f, "resolvedBy") is not None)
    assert engine.list("FailureEvent", {"isResolved": "false"}).total == 3


def test_maintenance_reason(engine, pg):
    """CORRECTIVE는 어떤 고장의 resolvedBy인 교체 기록이다 (결정 4의 SQL)."""
    text = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    decision4 = re.search(r"```sql\n(.*?)```", text.split("### resolvedBy", 1)[1], re.S)[1]
    with pg.cursor() as cur:
        cur.execute(decision4)
        expected = {maint_id for _, maint_id in cur.fetchall()}
    corrective = engine.list("Maintenance", {"reason": "CORRECTIVE", "limit": "500"})
    ids = {m.id for m in corrective.items}
    for offset in range(500, corrective.total, 500):
        page = {"reason": "CORRECTIVE", "limit": "500", "offset": str(offset)}
        ids |= {m.id for m in engine.list("Maintenance", page).items}
    assert ids == expected
    assert engine.list("Maintenance", {"reason": "PREVENTIVE"}).total == 3283 - len(expected)


def test_error_count_7d_boundary(engine, pg):
    """결정 19: now - 7일 < datetime <= now."""
    with pg.cursor() as cur:
        cur.execute(
            "SELECT machine_id, count(*) FROM raw.errors"
            " WHERE datetime > %s AND datetime <= %s GROUP BY machine_id",
            (NOW - timedelta(days=7), NOW),
        )
        expected = dict(cur.fetchall())
    assert {m.id: m.derived["errorCount7d"] for m in _machines(engine)} == {
        m: expected.get(m, 0) for m in range(1, 101)
    }


def test_last_replaced_at(engine):
    m17 = engine.get("Machine", 17)
    last = m17.derived["lastReplacedAt"]
    assert set(last) == {"comp1", "comp2", "comp3", "comp4"}
    # comp2는 2015-11-27 교체 기록을 숨겼다 (결정 14). 그보다 앞의 교체가 마지막이다
    assert last["comp2"] < "2015-11-27"


# ---------------------------------------------------------------------------
# 거르기와 정렬


def test_filter_and_sort_by_derived(engine):
    high = engine.list("Machine", {"riskLevel": "HIGH"})
    assert high.total == 5 and all(m.derived["riskLevel"] == "HIGH" for m in high.items)

    by_errors = engine.list("Machine", {"sort": "-errorCount7d", "limit": "500"}).items
    counts = [m.derived["errorCount7d"] for m in by_errors]
    assert counts == sorted(counts, reverse=True)
    top = [m for m in by_errors if m.derived["errorCount7d"] == counts[0]]
    assert [m.id for m in top] == sorted(m.id for m in top)  # 같은 값끼리는 machineId 순서

    page = engine.list("Machine", {"status__in": "DOWN,READY", "errorCount7d__gte": "1"})
    assert {m.id for m in page.items} <= {17, 88}
    assert engine.list("Machine", {"riskLevel": "HIGH", "status": "DOWN"}).total <= 2


def test_link_filter_by_derived(engine):
    """docs/api.md 2절의 예: GET /objects/Machine/17/links/hasFailures?isResolved=false"""
    page = engine.links("Machine", "17", "hasFailures", {"isResolved": "false"})
    assert [f.id for f in page.items] == [121]
    assert page.to_json()["items"][0]["derived"] == {"isResolved": False}


def test_explain_shows_now(engine):
    text = engine.render(engine.list("Machine", {"riskLevel": "HIGH"}).statement)
    assert text.startswith("WITH clock(now) AS (SELECT '2016-01-01 06:00:00'::timestamp)")


# ---------------------------------------------------------------------------
# 시계


def test_clock_is_injected(pg):
    """현재 시각은 생성자에 넘긴 clock에서만 얻는다. 시각을 돌리면 최근 7일도 따라 움직인다."""
    earlier = datetime(2015, 6, 1, 6, 0)
    times: list[datetime] = []

    def clock() -> datetime:
        times.append(earlier)
        return earlier

    engine = QueryEngine(spec.load(), pg, clock=clock)
    page = engine.list("Machine", {"limit": "500"})
    assert times == [earlier]  # 한 번의 list는 시계를 한 번 읽고 count와 select에 같이 쓴다
    assert page.statement.params[0] == earlier
    with pg.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.errors WHERE machine_id = 1 AND datetime > %s"
            " AND datetime <= %s",
            (earlier - timedelta(days=7), earlier),
        )
        (expected,) = cur.fetchone()
    assert page.items[0].derived["errorCount7d"] == expected


# ---------------------------------------------------------------------------
# status 전이 (결정 12, 21). GOV2 시나리오의 바탕


def _work_order(pg, machine_id, comp, failure_id=None) -> int:
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO ops.work_orders (machine_id, comp, type, status, assignee_id, created_by,"
            " created_at, failure_id) VALUES (%s, %s, %s, 'OPEN', 'mech1', 'mgr1', %s, %s)"
            " RETURNING work_order_id",
            (machine_id, comp, "CORRECTIVE" if failure_id else "PREVENTIVE", NOW, failure_id),
        )
        return cur.fetchone()[0]


def _complete(pg, work_order_id) -> None:
    """completeReplacement의 effect: 작업지시를 DONE으로, 교체 기록을 하나."""
    with pg.cursor() as cur:
        cur.execute(
            "UPDATE ops.work_orders SET status = 'DONE', completed_at = %s"
            " WHERE work_order_id = %s",
            (NOW, work_order_id),
        )
        cur.execute(
            "INSERT INTO ops.maintenance_log (datetime, machine_id, comp, work_order_id)"
            " SELECT %s, machine_id, comp, work_order_id FROM ops.work_orders"
            " WHERE work_order_id = %s",
            (NOW, work_order_id),
        )


def _restart(pg, resolver, machine_id) -> None:
    """restartMachine의 effect. lastRestartWorkOrderId는 actions.yaml의 식으로 구한다."""
    target = resolver.get("Machine", machine_id)
    names = {"target": target, "actor": None, "input": {}, "now": NOW}
    max_wo = compile_expr("max(w.workOrderId for w in target.hasWorkOrders)").evaluate(
        resolver, names
    )
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO ops.machine_state VALUES (%s, %s, 'mgr1', %s)"
            " ON CONFLICT (machine_id) DO UPDATE SET last_restart_at = EXCLUDED.last_restart_at,"
            " last_restart_by = EXCLUDED.last_restart_by,"
            " last_restart_work_order_id = EXCLUDED.last_restart_work_order_id",
            (machine_id, NOW, max_wo),
        )


def test_status_over_two_repair_cycles(engine, resolver, ops):
    def status() -> str:
        return engine.get("Machine", 17).derived["status"]

    assert status() == "DOWN"

    # 1바퀴: 데모 고장 121 수리
    wo1 = _work_order(ops, 17, "comp2", failure_id=121)
    assert status() == "MAINTENANCE"
    _complete(ops, wo1)
    assert resolver.get("FailureEvent", 121).derived["isResolved"] is True
    assert status() == "READY"
    _restart(ops, resolver, 17)
    assert status() == "RUNNING"

    # 2바퀴: 같은 시각에 예방 정비. 시각으로 비교했다면 READY가 되지 않는다 (결정 21)
    wo2 = _work_order(ops, 17, "comp4")
    assert status() == "MAINTENANCE"
    _complete(ops, wo2)
    assert status() == "READY"
    _restart(ops, resolver, 17)
    assert status() == "RUNNING"
    assert (
        engine.get(
            "Maintenance", resolver.attr(resolver.get("WorkOrder", wo2), "completedAs").id
        ).derived["reason"]
        == "PREVENTIVE"
    )


def test_resolver_recomputes_derived_on_the_same_record(resolver, ops):
    """액션 엔진은 effect 전에 잡아 둔 객체로 effect 뒤에 attr을 다시 부른다.

    Resolver는 객체를 읽을 때 채운 파생 속성 값을 쓰지 않고 부를 때마다 계산한다.
    """
    m17, f121 = resolver.get("Machine", 17), resolver.get("FailureEvent", 121)
    assert resolver.attr(m17, "status") == "DOWN"
    assert resolver.attr(f121, "isResolved") is False

    wo = _work_order(ops, 17, "comp2", failure_id=121)
    assert m17.derived["status"] == "DOWN"  # 읽을 때 채운 값은 그대로다
    assert resolver.attr(m17, "status") == "MAINTENANCE"

    _complete(ops, wo)
    assert resolver.attr(f121, "isResolved") is True
    assert resolver.attr(m17, "status") == "READY"


def test_derived_of_a_missing_record(engine, resolver):
    """다시 계산하려는 객체가 없어졌으면 엔진은 NOT_FOUND, 식 평가기에는 ExprError다."""
    ghost = ObjectRecord(type="Machine", id=999999, properties={})
    with pytest.raises(QueryError) as e:
        engine.derived_value(ghost, "status")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(ExprError, match="없어졌습니다"):
        resolver.attr(ghost, "status")


def test_open_work_order_covers_failure(engine, ops):
    """미해결 고장에 OPEN 작업지시가 걸리면 DOWN이 아니라 MAINTENANCE다."""
    _work_order(ops, 88, "comp2", failure_id=648)
    m88 = engine.get("Machine", 88)
    assert m88.derived["status"] == "DOWN"  # 649는 아직 걸리지 않았다
    _work_order(ops, 88, "comp4", failure_id=649)
    m88 = engine.get("Machine", 88)
    assert (m88.derived["status"], m88.derived["hasOpenFailure"]) == ("MAINTENANCE", True)


# ---------------------------------------------------------------------------
# 식 평가기와 함께 (actions.yaml의 사전조건)


def test_action_criteria_on_real_engine(resolver):
    m17, m49 = resolver.get("Machine", 17), resolver.get("Machine", 49)

    def run(source, target, **inputs):
        names = {"target": target, "actor": None, "input": inputs, "now": NOW}
        return compile_expr(source).evaluate(resolver, names)

    assert run("target.status == DOWN", m17) is True
    assert run("target.status == RUNNING", m49) is True
    assert run("not target.hasOpenFailure", m17) is False
    corrective = "any(not f.isResolved and f.comp == input.comp for f in target.hasFailures)"
    assert run(corrective, m17, comp="comp2") is True
    assert run(corrective, m17, comp="comp1") is False
    assert run("[f.failureId for f in target.hasFailures if not f.isResolved]", m17) == [121]
    assert run("Machine(88).riskLevel in [HIGH, MEDIUM, LOW]", m17) is True
