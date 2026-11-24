"""Machine.status의 sql이 수리와 재가동을 여러 번 반복해도 맞게 바뀌는지 본다.

CI에는 Postgres가 없어서 SQLite 메모리 DB에 status 계산에 필요한 테이블만 만들고,
objects.yaml의 자리표시자를 펼친 sql을 그대로 돌린다. status가 부르는 파생 속성
(hasUncoveredFailure, hasOpenWorkOrder, isResolved)은 Postgres 전용 문법을 쓰지 않는다.

기준 시각이 고정(결정 7)이라 액션이 남기는 시각은 모두 같다. 그래서 두 번째 수리에서
시각 비교가 틀렸었다 (결정 21).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)(?:\.([A-Za-z][A-Za-z0-9]*))?\}")
ALIAS_DECL = re.compile(r"\{([A-Z][A-Za-z0-9]*)\}\s+([a-z][a-z0-9_]*)")
NOW = "2016-01-01 06:00:00"

TABLES = """
CREATE TABLE core.machine (machine_id int, last_restart_at text, last_restart_by text,
                           last_restart_work_order_id int);
CREATE TABLE raw.failures (failure_id int, datetime text, machine_id int, comp text);
CREATE TABLE core.maintenance (maint_id int, datetime text, machine_id int, comp text,
                               source text, work_order_id int);
CREATE TABLE ops.work_orders (work_order_id int, machine_id int, comp text, status text,
                              completed_at text, failure_id int);
"""


@pytest.fixture(scope="module")
def objects() -> dict[str, Any]:
    with (ROOT / "ontology" / "objects.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def expand(objects: dict[str, Any], type_name: str, prop: str, alias: str) -> str:
    """type_name.prop의 sql을 펼친다. alias는 그 객체 행을 가리키는 SQL 별칭이다."""
    obj = objects[type_name]
    sql = obj["derived"][prop]["sql"]
    aliases = {a: t for t, a in ALIAS_DECL.findall(sql)}

    def sub(m: re.Match[str]) -> str:
        head, tail = m[1], m[2]
        if tail:
            return f"({expand(objects, aliases[head], tail, head)})"
        if head in objects:
            return objects[head]["source"]
        if head == "now":
            return f"'{NOW}'"
        if head in obj["properties"]:
            return f"{alias}.{obj['properties'][head]['column']}"
        return f"({expand(objects, type_name, head, alias)})"

    return PLACEHOLDER.sub(sub, sql)


class Db:
    """액션이 남기는 행을 그대로 흉내 낸다. 시각은 모두 NOW다."""

    def __init__(self, objects: dict[str, Any]) -> None:
        self.con = sqlite3.connect(":memory:")
        for schema in ("core", "raw", "ops"):
            self.con.execute(f"ATTACH ':memory:' AS {schema}")
        self.con.executescript(TABLES)
        self.status_sql = (
            f"SELECT {expand(objects, 'Machine', 'status', 't')} "
            "FROM core.machine t WHERE t.machine_id = ?"
        )
        self.next_wo = 1

    def status(self, machine_id: int) -> str:
        return self.con.execute(self.status_sql, (machine_id,)).fetchone()[0]

    def create_work_order(self, machine_id: int, comp: str, failure_id: int | None = None) -> int:
        wo = self.next_wo
        self.next_wo += 1
        self.con.execute(
            "INSERT INTO ops.work_orders VALUES (?, ?, ?, 'OPEN', NULL, ?)",
            (wo, machine_id, comp, failure_id),
        )
        return wo

    def complete_replacement(self, wo: int) -> None:
        self.con.execute(
            "UPDATE ops.work_orders SET status = 'DONE', completed_at = ? WHERE work_order_id = ?",
            (NOW, wo),
        )
        self.con.execute(
            "INSERT INTO core.maintenance "
            "SELECT 3286 + work_order_id, ?, machine_id, comp, 'ACTION', work_order_id "
            "FROM ops.work_orders WHERE work_order_id = ?",
            (NOW, wo),
        )

    def restart(self, machine_id: int) -> None:
        # restartMachine의 effect: max(w.workOrderId for w in target.hasWorkOrders)
        (max_wo,) = self.con.execute(
            "SELECT max(work_order_id) FROM ops.work_orders WHERE machine_id = ?", (machine_id,)
        ).fetchone()
        self.con.execute(
            "UPDATE core.machine SET last_restart_at = ?, last_restart_by = 'mgr1', "
            "last_restart_work_order_id = ? WHERE machine_id = ?",
            (NOW, max_wo, machine_id),
        )


def test_restart_sets_last_work_order_id():
    with (ROOT / "ontology" / "actions.yaml").open(encoding="utf-8") as f:
        actions = yaml.safe_load(f)
    effects = {e["set"]["field"]: e["set"]["value"] for e in actions["restartMachine"]["effect"]}
    assert effects["lastRestartWorkOrderId"] == "max(w.workOrderId for w in target.hasWorkOrders)"


def test_status_over_two_repair_cycles(objects):
    db = Db(objects)
    db.con.execute("INSERT INTO core.machine VALUES (17, NULL, NULL, NULL)")
    db.con.execute("INSERT INTO raw.failures VALUES (121, '2015-11-27 06:00:00', 17, 'comp2')")
    assert db.status(17) == "DOWN"

    # 1바퀴: 고장 수리
    wo1 = db.create_work_order(17, "comp2", failure_id=121)
    assert db.status(17) == "MAINTENANCE"
    db.complete_replacement(wo1)
    assert db.status(17) == "READY"
    db.restart(17)
    assert db.status(17) == "RUNNING"

    # 2바퀴: 같은 설비를 같은 시각에 다시 수리한다. 시각 비교였다면 여기서 RUNNING에 머문다
    wo2 = db.create_work_order(17, "comp4")
    assert db.status(17) == "MAINTENANCE"
    db.complete_replacement(wo2)
    assert db.status(17) == "READY"
    db.restart(17)
    assert db.status(17) == "RUNNING"


def test_other_machine_work_orders_do_not_count(objects):
    """작업지시 번호는 설비가 함께 쓴다. 다른 설비의 작업지시가 READY를 만들지 않는다."""
    db = Db(objects)
    db.con.execute("INSERT INTO core.machine VALUES (17, NULL, NULL, NULL)")
    db.con.execute("INSERT INTO core.machine VALUES (88, NULL, NULL, NULL)")
    wo1 = db.create_work_order(17, "comp2")
    db.complete_replacement(wo1)
    db.restart(17)
    wo2 = db.create_work_order(88, "comp4")
    db.complete_replacement(wo2)
    assert db.status(17) == "RUNNING"
    assert db.status(88) == "READY"
