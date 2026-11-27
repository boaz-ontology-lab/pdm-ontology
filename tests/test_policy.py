"""조회 정책(#19): policies.yaml + policy_tags.yaml 로더와, 액션 엔진의 대상 찾기 404."""

from __future__ import annotations

import pytest
from action_memory_store import NOW, demo_store

from app.action.engine import ActionEngine
from app.action.spec import load_actions
from app.policy.policy import PolicyError, load_policy, load_visibility, parse_policy

POLICY = load_policy()
VENDOR_TABLES = {
    "core.machine",
    "core.maintenance",
    "ops.inspection_requests",
    "ops.work_orders",
    "raw.errors",
    "raw.failures",
    "raw.telemetry",
}
SENSORS = {"volt", "rotate", "pressure", "vibration"}


# ---------------------------------------------------------------------------
# 로더: 실제 ontology 파일
# ---------------------------------------------------------------------------


def test_vendor_policy_from_real_files():
    vendor = POLICY.for_role("VENDOR")
    assert set(vendor.rules_by_table) == VENDOR_TABLES
    assert {r.name for t in VENDOR_TABLES for r in vendor.row_rules(t)} == {"ownVendorMachines"}
    assert vendor.masked_columns("raw.telemetry") == SENSORS
    assert vendor.masked_columns("core.machine") == frozenset()


@pytest.mark.parametrize("role", ["FIELD_WORKER", "MECHANIC", "MANAGER", None])
def test_other_roles_are_unrestricted(role):
    policy = POLICY.for_role(role)
    assert not policy.restricted
    assert policy.row_rules("core.machine") == ()


def test_row_rule_sql():
    rule = POLICY.for_role("VENDOR").row_rules("raw.failures")[0]
    query, params = rule.sql("f", {"vendorId": 1, "userId": "venA"})
    assert query.as_string(None) == (
        '"f"."machine_id" IN (SELECT "machine_id" FROM "core"."machine" WHERE "vendor_id" = %s)'
    )
    assert params == (1,)


def test_explain():
    vendor = POLICY.for_role("VENDOR")
    assert vendor.explain("raw.telemetry") == {
        "rowFilter": [
            {
                "rule": "ownVendorMachines",
                "description": "자기 회사가 납품한 설비와 그 설비의 기록만 보인다 (결정 5)",
            }
        ],
        "maskedColumns": ["pressure", "rotate", "vibration", "volt"],
    }
    assert POLICY.for_role("MANAGER").explain("raw.telemetry") == {}


# ---------------------------------------------------------------------------
# 로더: 틀린 정의
# ---------------------------------------------------------------------------

RULE = {
    "description": "x",
    "column": "machine_id",
    "lookup": "core.machine",
    "key": "machine_id",
    "where": {"vendor_id": "actor.vendorId"},
}
TAGS = {
    "tags": {
        "VendorScoped": {"tables": ["raw.errors"]},
        "ProcessParameter": {"columns": ["raw.telemetry.volt"]},
    }
}


@pytest.mark.parametrize(
    ("roles", "rules", "message"),
    [
        ({"VENDOR": {"row_filter": [{"tag": "VendorScoped", "rule": "nope"}]}}, {}, "없는 규칙"),
        (
            {"VENDOR": {"row_filter": [{"tag": "ProcessParameter", "rule": "r"}]}},
            {"r": RULE},
            "컬럼에 붙는 태그",
        ),
        ({"VENDOR": {"mask": [{"tag": "VendorScoped"}]}}, {}, "테이블에 붙는 태그"),
        ({}, {"r": {**RULE, "where": {"vendor_id": "1 OR 1=1"}}}, "actor.속성"),
        ({}, {"r": {**RULE, "where": {}}}, "where가 비었다"),
    ],
)
def test_bad_policy(roles, rules, message):
    with pytest.raises(PolicyError, match=message):
        parse_policy({"roles": roles, "rules": rules}, TAGS)


def test_tag_with_no_targets_means_nothing_to_filter():
    """OpenMetadata에서 태그를 모두 떼면 스냅샷에서 태그가 빠진다. 그때는 거를 것이 없다."""
    policy = parse_policy(
        {"roles": {"VENDOR": {"row_filter": [{"tag": "VendorScoped", "rule": "r"}]}},
         "rules": {"r": RULE}},
        {"tags": {}},
    )  # fmt: skip
    assert not policy.for_role("VENDOR").restricted


def test_snapshot_change_changes_scope():
    """데모 3: 카탈로그에서 raw.maintenance에 태그를 붙이고 동기화하면 그 테이블도 걸러진다."""
    policies = {
        "roles": {"VENDOR": {"row_filter": [{"tag": "VendorScoped", "rule": "r"}]}},
        "rules": {"r": RULE},
    }
    before = parse_policy(policies, TAGS).for_role("VENDOR")
    tags = {"tags": {"VendorScoped": {"tables": ["raw.errors", "raw.maintenance"]}}}
    after = parse_policy(policies, tags).for_role("VENDOR")
    assert before.row_rules("raw.maintenance") == ()
    assert after.row_rules("raw.maintenance")[0].name == "r"


# ---------------------------------------------------------------------------
# 액션 엔진: 대상 찾기 단계
# ---------------------------------------------------------------------------

VISIBILITY = load_visibility(POLICY)


@pytest.fixture
def store():
    return demo_store()


@pytest.mark.parametrize(
    ("user", "type_name", "object_id", "visible"),
    [
        ("venA", "Machine", 17, True),  # model1, 벤더 A
        ("venA", "Machine", 49, True),  # model2, 벤더 A
        ("venA", "Machine", 88, False),  # model4, 벤더 B
        ("venB", "Machine", 88, True),
        ("venB", "Machine", 17, False),
        ("venA", "FailureEvent", 121, True),  # 설비 17의 고장
        ("venA", "FailureEvent", 648, False),  # 설비 88의 고장
        ("mgr1", "Machine", 88, True),  # 벤더가 아니면 제한 없음
        ("fw1", "FailureEvent", 648, True),
    ],
)
def test_visibility(store, user, type_name, object_id, visible):
    actor = store.get("User", user)
    obj = store.get(type_name, object_id)
    assert VISIBILITY.allows(store, actor, type_name, obj) is visible


def test_visibility_follows_machine_of_work_order(store):
    """작업지시는 machineId로 설비를 찾아 그 벤더를 본다. 다른 회사 작업지시 번호가 새지 않는다."""
    wo = store.create("WorkOrder", {
        "machineId": 88, "comp": "comp2", "type": "CORRECTIVE", "status": "OPEN",
        "assigneeId": "mech1", "createdBy": "mgr1", "createdAt": NOW, "completedAt": None,
        "failureId": 648, "inspectionRequestId": None,
    })  # fmt: skip
    assert not VISIBILITY.allows(store, store.get("User", "venA"), "WorkOrder", wo)
    assert VISIBILITY.allows(store, store.get("User", "venB"), "WorkOrder", wo)


@pytest.fixture
def engine(store):
    return ActionEngine(load_actions(), store, clock=lambda: NOW, visibility=VISIBILITY)


def test_vendor_gets_404_for_other_vendors_machine(engine, store):
    res = engine.execute("restartMachine", "venA", {"machineId": 88})
    assert res.status == 404
    assert res.body["reasonCode"] == "NOT_FOUND"
    assert res.body["violations"][0]["detail"] == {"type": "Machine", "id": 88}
    # 없는 설비와 응답이 같다
    missing = engine.execute("restartMachine", "venA", {"machineId": 999})
    assert {k: v for k, v in missing.body.items() if k != "actionId"} == {
        **{k: v for k, v in res.body.items() if k != "actionId"},
        "violations": [{"code": "NOT_FOUND", "detail": {"type": "Machine", "id": 999}}],
    }
    assert store.audit[0].reason_code == "NOT_FOUND"  # 404도 감사 로그에 남는다


def test_vendor_gets_403_for_own_machine(engine):
    """자기 회사 설비는 보이므로 권한 단계까지 가서 403이다."""
    res = engine.execute("restartMachine", "venA", {"machineId": 17})
    assert (res.status, res.body["reasonCode"]) == (403, "ROLE_NOT_ALLOWED")


def test_other_roles_unchanged(engine):
    res = engine.execute("restartMachine", "mgr1", {"machineId": 88})
    assert (res.status, res.body["reasonCode"]) == (409, "UNRESOLVED_FAILURE")
