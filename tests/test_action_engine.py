"""액션 엔진 테스트.

Notion C 문서의 테스트 케이스(P 권한, C 사전조건, S 성공, A 감사 로그)를 옮겼다.

C 문서와 달라진 것:
- C3(comp5)는 사전조건이 아니라 입력 오류 422다 (결정 16, 18)
- 설비와 고장 번호는 실제 데이터와 결정 14를 따른다. 데모 고장은 설비 17의 121,
  동시 고장은 C 문서의 M5 대신 설비 88의 648(comp2), 649(comp4)다
"""

from __future__ import annotations

from typing import Any

import pytest
from action_memory_store import NOW, ConstraintViolation, MemoryStore, demo_store

from app.action.engine import ActionEngine, ActionResponse
from app.action.expr import ExprError
from app.action.spec import SpecError, load_actions, parse_actions
from app.action.store import StoreConflict

SPEC = load_actions()


@pytest.fixture
def store() -> MemoryStore:
    return demo_store()


@pytest.fixture
def engine(store: MemoryStore) -> ActionEngine:
    return ActionEngine(SPEC, store, clock=lambda: NOW)


def run(engine: ActionEngine, action: str, user: str | None, **body: Any) -> ActionResponse:
    return engine.execute(action, user, body)


def status_of(store: MemoryStore, machine_id: int) -> str:
    return store.attr(store.get("Machine", machine_id), "status")


def create_wo(engine: ActionEngine, machine_id: int, comp: str, **extra: Any) -> int:
    body = {"type": "CORRECTIVE", "assigneeId": "mech1", **extra}
    res = run(engine, "createWorkOrder", "mgr1", machineId=machine_id, comp=comp, **body)
    assert res.status == 201, res.body
    return res.body["created"][0]["id"]


def codes(res: ActionResponse) -> list[str]:
    return [v["code"] for v in res.body["violations"]]


# ---------------------------------------------------------------------------
# 권한 (P)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "action", "user", "body"),
    [
        ("P1", "requestInspection", "venA", {"machineId": 49, "reason": "소음"}),
        ("P2", "createWorkOrder", "mech1", {"machineId": 17, "comp": "comp2",
                                            "type": "CORRECTIVE", "assigneeId": "mech1"}),
        ("P3", "createWorkOrder", "fw1", {"machineId": 17, "comp": "comp2",
                                          "type": "CORRECTIVE", "assigneeId": "mech1"}),
        ("P6", "restartMachine", "fw1", {"machineId": 17}),
        ("P7", "restartMachine", "mech1", {"machineId": 17}),
    ],
)  # fmt: skip
def test_role_not_allowed(engine, case, action, user, body):
    res = engine.execute(action, user, body)
    assert res.status == 403, case
    assert res.body["stage"] == "PERMISSION"
    assert codes(res) == ["ROLE_NOT_ALLOWED"]


def test_p4_manager_cannot_complete_replacement(engine):
    """직무 분리: 작업지시를 만든 관리자도 교체 완료는 못 한다."""
    wo = create_wo(engine, 17, "comp2")
    res = run(engine, "completeReplacement", "mgr1", workOrderId=wo)
    assert res.status == 403
    assert codes(res) == ["ROLE_NOT_ALLOWED"]
    assert res.body["violations"][0]["detail"] == {"role": "MANAGER", "allowedRoles": ["MECHANIC"]}


def test_p5_not_assignee(engine):
    wo = create_wo(engine, 17, "comp2")
    res = run(engine, "completeReplacement", "mech2", workOrderId=wo)
    assert res.status == 403
    assert codes(res) == ["NOT_ASSIGNEE"]
    assert res.body["violations"][0]["detail"] == {"assigneeId": "mech1"}


def test_p8_permission_checked_before_precondition(engine):
    """M17은 미해결 고장도 있지만, 권한에서 막히면 사전조건은 보지 않는다."""
    res = run(engine, "restartMachine", "fw1", machineId=17)
    assert codes(res) == ["ROLE_NOT_ALLOWED"]


# ---------------------------------------------------------------------------
# 사전조건 (C)
# ---------------------------------------------------------------------------


def test_c1_inspection_needs_running(engine):
    res = run(engine, "requestInspection", "fw1", machineId=17, reason="소음")
    assert res.status == 409
    assert codes(res) == ["INVALID_STATE"]
    assert res.body["violations"][0]["detail"] == {"currentStatus": "DOWN"}


def test_c2_duplicate_inspection(engine):
    assert run(engine, "requestInspection", "fw1", machineId=49, reason="소음").status == 201
    res = run(engine, "requestInspection", "fw2", machineId=49, reason="진동")
    assert res.status == 409
    assert codes(res) == ["DUPLICATE_REQUEST"]
    existing = res.body["violations"][0]["detail"]["existingRequests"]
    assert [r["inspectionRequestId"] for r in existing] == [1]


def test_c3_invalid_component_is_input_error(engine):
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp5",
              type="CORRECTIVE", assigneeId="mech1")  # fmt: skip
    assert res.status == 422
    assert res.body["result"] == "ERROR"
    assert res.body["stage"] == "INPUT"
    assert res.body["violations"][0]["detail"]["field"] == "comp"


def test_c4_assignee_must_be_mechanic(engine):
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="CORRECTIVE", assigneeId="fw1")  # fmt: skip
    assert res.status == 409
    assert codes(res) == ["INVALID_ASSIGNEE"]


def test_c5_corrective_needs_unresolved_failure(engine):
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp1",
              type="CORRECTIVE", assigneeId="mech1")  # fmt: skip
    assert codes(res) == ["INVALID_STATE"]
    assert res.body["message"] == "comp1에 미해결 고장이 없어 고장 수리 작업지시를 만들 수 없습니다"


def test_c6_preventive_needs_running(engine):
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp1",
              type="PREVENTIVE", assigneeId="mech1")  # fmt: skip
    assert codes(res) == ["INVALID_STATE"]
    assert res.body["violations"][0]["detail"] == {"currentStatus": "DOWN"}


def test_c7_duplicate_open_work_order(engine):
    create_wo(engine, 17, "comp2")
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="CORRECTIVE", assigneeId="mech2")  # fmt: skip
    assert res.status == 409
    assert codes(res) == ["OPEN_WORK_ORDER"]


def test_c8_complete_done_work_order(engine):
    wo = create_wo(engine, 17, "comp2")
    assert run(engine, "completeReplacement", "mech1", workOrderId=wo).status == 201
    res = run(engine, "completeReplacement", "mech1", workOrderId=wo)
    assert codes(res) == ["INVALID_STATE"]
    assert res.body["message"] == "작업지시가 DONE 상태라 교체 완료할 수 없습니다"


def test_c9_restart_with_unresolved_failure(engine):
    """데모 2번 두 번째 장면. api.md 예시처럼 사유는 UNRESOLVED_FAILURE 하나다."""
    res = run(engine, "restartMachine", "mgr1", machineId=17)
    assert res.status == 409
    assert codes(res) == ["UNRESOLVED_FAILURE"]
    assert res.body["message"] == "미해결 고장 1건이 남아 있어 재가동할 수 없습니다"
    failures = res.body["violations"][0]["detail"]["failures"]
    assert [(f["failureId"], f["comp"], f["datetime"]) for f in failures] == [
        (121, "comp2", "2015-11-27T06:00:00")
    ]


def test_c10_restart_reports_all_failed_conditions(engine):
    create_wo(engine, 17, "comp2")
    res = run(engine, "restartMachine", "mgr1", machineId=17)
    assert codes(res) == ["UNRESOLVED_FAILURE", "OPEN_WORK_ORDER"]
    assert res.body["reasonCode"] == "UNRESOLVED_FAILURE"


def test_c11_restart_running_machine(engine):
    res = run(engine, "restartMachine", "mgr1", machineId=49)
    assert codes(res) == ["INVALID_STATE"]
    assert (
        res.body["message"]
        == "설비가 RUNNING 상태라 재가동할 수 없습니다. READY일 때만 재가동합니다"
    )


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"failureId": 300}, "INVALID_STATE"),  # 다른 설비의 고장
        ({"failureId": 999}, None),  # 없는 고장은 입력 오류 422
        ({"inspectionRequestId": 999}, None),
    ],
)
def test_reference_checks(engine, extra, expected):
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="CORRECTIVE", assigneeId="mech1", **extra)  # fmt: skip
    if expected is None:
        assert res.status == 422
    else:
        assert res.status == 409
        assert codes(res) == [expected]


# ---------------------------------------------------------------------------
# 성공과 상태 변화 (S)
# ---------------------------------------------------------------------------


def test_s1_request_inspection(engine, store):
    res = run(engine, "requestInspection", "fw1", machineId=49, reason="압력이 평소보다 높음")
    assert res.status == 201
    created = res.body["created"][0]
    assert created["type"] == "InspectionRequest"
    assert created["status"] == "REQUESTED"
    assert created["evidence"] == {"errorCount7d": 3, "riskLevel": "HIGH"}  # 서버가 채운다
    assert res.body["changes"] == []
    assert status_of(store, 49) == "RUNNING"


def test_s1_to_s4_demo_2(engine, store):
    """데모 2번: 작업지시 → 교체 완료 → 재가동. api.md 3장 예시와 같은 흐름이다."""
    # S2 작업지시: failureId를 보내지 않아도 comp2 미해결 고장 121을 잇는다
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="CORRECTIVE", assigneeId="mech1")  # fmt: skip
    assert res.status == 201
    assert res.body["created"][0]["failureId"] == 121
    assert res.body["changes"] == [
        {"type": "Machine", "id": 17, "field": "status", "before": "DOWN", "after": "MAINTENANCE"}
    ]
    assert res.body["remaining"] == {"uncoveredFailures": []}
    wo = res.body["created"][0]["id"]

    # S3 교체 완료
    res = run(engine, "completeReplacement", "mech1", workOrderId=wo)
    assert res.status == 201
    maint = res.body["created"][0]
    assert (maint["type"], maint["id"], maint["source"]) == ("Maintenance", 3287, "ACTION")
    assert maint["datetime"] == "2016-01-01T06:00:00"
    assert [(c["type"], c["field"], c["before"], c["after"]) for c in res.body["changes"]] == [
        ("WorkOrder", "status", "OPEN", "DONE"),
        ("WorkOrder", "completedAt", None, "2016-01-01T06:00:00"),
        ("FailureEvent", "isResolved", False, True),
        ("Machine", "status", "MAINTENANCE", "READY"),
    ]
    assert res.body["remaining"] == {"unresolvedFailures": [], "openWorkOrders": []}

    # S4 재가동
    res = run(engine, "restartMachine", "mgr1", machineId=17)
    assert res.status == 200
    assert res.body["created"] == []
    assert [(c["field"], c["before"], c["after"]) for c in res.body["changes"]] == [
        ("lastRestartAt", None, "2016-01-01T06:00:00"),
        ("lastRestartBy", None, "mgr1"),
        ("lastRestartWorkOrderId", None, wo),
        ("status", "READY", "RUNNING"),
    ]
    assert status_of(store, 17) == "RUNNING"


def test_second_cycle_after_restart(engine, store):
    """결정 21: 기준 시각이 멈춰 있어도, 재가동 뒤 같은 시각에 끝난 교체로 다시 READY가 된다."""
    wo = create_wo(engine, 17, "comp2")
    run(engine, "completeReplacement", "mech1", workOrderId=wo)
    assert run(engine, "restartMachine", "mgr1", machineId=17).status == 200
    assert store.get("Machine", 17).props["lastRestartWorkOrderId"] == wo

    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp1",
              type="PREVENTIVE", assigneeId="mech1")  # fmt: skip
    assert status_of(store, 17) == "MAINTENANCE"
    run(engine, "completeReplacement", "mech1", workOrderId=res.body["created"][0]["id"])
    assert status_of(store, 17) == "READY"  # 시각(completedAt > lastRestartAt)으로 가리면 RUNNING
    assert run(engine, "restartMachine", "mgr1", machineId=17).status == 200
    assert status_of(store, 17) == "RUNNING"


def test_s5_to_s7_simultaneous_failures(engine, store):
    # S5 comp2만 작업지시: comp4가 아직 덮이지 않아 DOWN 그대로
    res = run(engine, "createWorkOrder", "mgr1", machineId=88, comp="comp2",
              type="CORRECTIVE", assigneeId="mech1")  # fmt: skip
    assert res.status == 201
    assert res.body["created"][0]["failureId"] == 648
    assert res.body["changes"] == []
    assert [f["failureId"] for f in res.body["remaining"]["uncoveredFailures"]] == [649]
    assert status_of(store, 88) == "DOWN"
    wo1 = res.body["created"][0]["id"]

    # S6 comp4 작업지시 → MAINTENANCE, comp2만 교체 → MAINTENANCE 그대로
    wo2 = create_wo(engine, 88, "comp4")
    assert status_of(store, 88) == "MAINTENANCE"
    res = run(engine, "completeReplacement", "mech1", workOrderId=wo1)
    assert status_of(store, 88) == "MAINTENANCE"
    assert [f["failureId"] for f in res.body["remaining"]["unresolvedFailures"]] == [649]
    assert [w["workOrderId"] for w in res.body["remaining"]["openWorkOrders"]] == [wo2]

    # S7 comp4 교체 → READY
    res = run(engine, "completeReplacement", "mech1", workOrderId=wo2)
    assert res.body["changes"][-1]["after"] == "READY"


def test_s8_preventive_from_inspection(engine, store):
    run(engine, "requestInspection", "fw1", machineId=49, reason="소음")
    res = run(engine, "createWorkOrder", "mgr1", machineId=49, comp="comp3",
              type="PREVENTIVE", assigneeId="mech2", inspectionRequestId=1)  # fmt: skip
    assert res.status == 201
    assert res.body["created"][0]["failureId"] is None
    assert [(c["type"], c["field"], c["after"]) for c in res.body["changes"]] == [
        ("InspectionRequest", "status", "CONVERTED"),
        ("Machine", "status", "MAINTENANCE"),
    ]
    wo = res.body["created"][0]["id"]

    # 교체 완료하면 근거 점검 요청은 CLOSED
    res = run(engine, "completeReplacement", "mech2", workOrderId=wo)
    assert ("InspectionRequest", "status", "CLOSED") in [
        (c["type"], c["field"], c["after"]) for c in res.body["changes"]
    ]
    assert status_of(store, 49) == "READY"


def test_converted_request_cannot_be_used_twice(engine):
    run(engine, "requestInspection", "fw1", machineId=49, reason="소음")
    body = {"machineId": 49, "type": "PREVENTIVE", "assigneeId": "mech2", "inspectionRequestId": 1}
    assert run(engine, "createWorkOrder", "mgr1", comp="comp3", **body).status == 201
    res = run(engine, "createWorkOrder", "mgr1", comp="comp4", **body)
    # PREVENTIVE인데 설비가 MAINTENANCE이고, 점검 요청도 이미 CONVERTED
    assert codes(res) == ["INVALID_STATE", "INVALID_STATE"]


# ---------------------------------------------------------------------------
# 사용자 확인, 입력, 대상
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user", [None, "", "ghost"])
def test_unknown_user(engine, user):
    res = run(engine, "restartMachine", user, machineId=17)
    assert res.status == 401
    assert (res.body["result"], res.body["stage"], res.body["reasonCode"]) == (
        "ERROR",
        "AUTH",
        "UNKNOWN_USER",
    )


def test_unknown_action_and_target(engine):
    res = run(engine, "deleteMachine", "mgr1", machineId=17)
    assert (res.status, res.body["reasonCode"]) == (404, "NOT_FOUND")
    res = run(engine, "restartMachine", "mgr1", machineId=999)
    assert (res.status, res.body["reasonCode"]) == (404, "NOT_FOUND")
    assert res.body["violations"][0]["detail"] == {"type": "Machine", "id": 999}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "machineId"),
        ({"machineId": "17"}, "machineId"),
        ({"machineId": True}, "machineId"),
        ({"machineId": 17, "force": True}, "force"),
    ],
)
def test_invalid_input(engine, body, field):
    res = engine.execute("restartMachine", "mgr1", body)
    assert res.status == 422
    assert res.body["reasonCode"] == "INVALID_INPUT"
    assert [v["detail"]["field"] for v in res.body["violations"]] == [field]


def test_input_rules(engine):
    long = "가" * 501
    res = run(engine, "requestInspection", "fw1", machineId=49, reason=long)
    assert res.status == 422
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="corrective", assigneeId="nobody")  # fmt: skip
    assert [v["detail"]["field"] for v in res.body["violations"]] == ["type", "assigneeId"]
    assert engine.execute("restartMachine", "mgr1", ["not", "object"]).status == 422


# ---------------------------------------------------------------------------
# 감사 로그 (A)
# ---------------------------------------------------------------------------


def test_a1_every_call_is_audited(engine, store):
    responses = [
        run(engine, "restartMachine", "ghost", machineId=17),  # 401
        run(engine, "restartMachine", "fw1", machineId=17),  # 403
        run(engine, "restartMachine", "mgr1", machineId=17),  # 409
        run(engine, "requestInspection", "fw1", machineId=49, reason="소음"),  # 201
    ]
    assert [r.body["actionId"] for r in responses] == [1, 2, 3, 4]
    assert [(a.result, a.stage, a.reason_code) for a in store.audit] == [
        ("ERROR", "AUTH", "UNKNOWN_USER"),
        ("DENIED", "PERMISSION", "ROLE_NOT_ALLOWED"),
        ("DENIED", "PRECONDITION", "UNRESOLVED_FAILURE"),
        ("ALLOWED", None, None),
    ]
    denied = store.audit[1]
    assert (denied.actor_id, denied.actor_role) == ("fw1", "FIELD_WORKER")
    assert (denied.target_type, denied.target_id) == ("Machine", 17)
    assert denied.input == {"machineId": 17}
    assert denied.at == NOW
    assert "actionId" not in denied.response  # 행 번호라 응답 본문에는 넣지 않는다


def test_a2_denied_call_changes_nothing(engine, store):
    before = {t: [dict(o.props) for o in objs] for t, objs in store.objects.items()}
    run(engine, "restartMachine", "mgr1", machineId=17)
    run(engine, "completeReplacement", "mgr1", workOrderId=1)
    after = {t: [dict(o.props) for o in objs] for t, objs in store.objects.items()}
    assert before == after


def test_sets_on_same_object_are_saved_together(engine, store):
    """연달아 바꾸는 set은 한 번에 저장한다. 하나씩 저장하면 db/05_ops.sql의 CHECK에 걸린다."""
    wo = store.get("WorkOrder", create_wo(engine, 17, "comp2"))
    with pytest.raises(ConstraintViolation):
        store.update(wo, {"status": "DONE"})  # completedAt 없이 DONE
    machine = store.get("Machine", 49)
    with pytest.raises(ConstraintViolation):
        store.update(machine, {"lastRestartAt": NOW})  # 승인자 없이 재가동 시각


def test_store_conflict_becomes_409_and_rolls_back(store):
    """동시 요청이 사전조건을 함께 통과해 DB 제약에 걸린 경우."""

    class RacingStore(MemoryStore):
        def create(self, type_name, values):
            super().create(type_name, values)  # 만든 뒤 제약에 걸린 것처럼
            raise StoreConflict("OPEN_WORK_ORDER")

    racing = RacingStore(objects=store.objects, next_id=store.next_id)
    engine = ActionEngine(SPEC, racing, clock=lambda: NOW)
    res = run(engine, "createWorkOrder", "mgr1", machineId=17, comp="comp2",
              type="CORRECTIVE", assigneeId="mech1")  # fmt: skip
    assert (res.status, codes(res)) == (409, ["OPEN_WORK_ORDER"])
    assert racing.objects["WorkOrder"] == []
    assert racing.next_id["WorkOrder"] == 1
    assert racing.audit[-1].reason_code == "OPEN_WORK_ORDER"


def test_response_shape_matches_api_doc(engine):
    res = run(engine, "requestInspection", "fw1", machineId=49, reason="소음")
    assert list(res.body) == [
        "actionId", "action", "result", "at", "actor", "target", "created", "changes", "remaining",
    ]  # fmt: skip
    assert res.body["actor"] == {"userId": "fw1", "role": "FIELD_WORKER"}
    assert res.body["target"] == {"type": "Machine", "id": 49}
    res = run(engine, "restartMachine", "fw1", machineId=17)
    assert list(res.body) == ["result", "stage", "reasonCode", "message", "violations", "actionId"]


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------


def _minimal_action(**overrides: Any) -> dict[str, Any]:
    action = {
        "description": "시험",
        "target": "Machine",
        "input": {"machineId": {"type": "int", "required": True}},
        "allowed_roles": ["MANAGER"],
        "criteria": [{"description": "x", "check": "true", "reason": "NOT_ASSIGNEE"}],
        "effect": [{"set": {"object": "target", "field": "lastRestartAt", "value": "now"}}],
    }
    action.update(overrides)
    return action


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({}, "criteria에 둘 수 없습니다"),  # PERMISSION 코드를 사전조건에 씀
        ({"criteria": [{"description": "x", "check": "true", "reason": "NOPE"}]}, "NOPE"),
        ({"input": {"id": {"type": "int", "required": True}}}, "machineId"),
        ({"criteria": [{"description": "x", "check": "open('f')", "reason": "NOPE"}]}, "open"),
    ],
)
def test_loader_rejects_bad_spec(overrides, message):
    data = {
        "reason_codes": {"NOT_ASSIGNEE": {"stage": "PERMISSION", "message": "담당자가 아님"}},
        "restartMachine": _minimal_action(**overrides),
    }
    with pytest.raises((SpecError, ExprError), match=message):
        parse_actions(data)
