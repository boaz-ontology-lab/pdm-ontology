"""액션 API와 감사 로그 API를 HTTP로 검사한다. 판정 자체는 test_action_engine.py가 본다."""

from __future__ import annotations

import pytest
from action_memory_store import NOW, MemoryStore, demo_store
from fastapi.testclient import TestClient

from app.action.engine import ActionEngine
from app.action.spec import load_actions
from app.main import create_app

SPEC = load_actions()


@pytest.fixture
def store() -> MemoryStore:
    return demo_store()


@pytest.fixture
def client(store: MemoryStore) -> TestClient:
    return TestClient(create_app(ActionEngine(SPEC, store, clock=lambda: NOW)))


def post(client: TestClient, action: str, user: str | None, body):
    headers = {"X-User-Id": user} if user else {}
    return client.post(f"/actions/{action}", json=body, headers=headers)


def test_demo_2_over_http(client):
    """데모 2번: 권한 거부 → 사전조건 거부 → 작업지시 → 교체 완료 → 재가동."""
    res = post(client, "restartMachine", "fw1", {"machineId": 17})
    assert (res.status_code, res.json()["reasonCode"]) == (403, "ROLE_NOT_ALLOWED")

    res = post(client, "restartMachine", "mgr1", {"machineId": 17})
    assert (res.status_code, res.json()["reasonCode"]) == (409, "UNRESOLVED_FAILURE")

    body = {"machineId": 17, "comp": "comp2", "type": "CORRECTIVE", "assigneeId": "mech1"}
    res = post(client, "createWorkOrder", "mgr1", body)
    assert res.status_code == 201
    work_order_id = res.json()["created"][0]["id"]

    res = post(client, "completeReplacement", "mech1", {"workOrderId": work_order_id})
    assert res.status_code == 201
    assert res.json()["changes"][-1] == {
        "type": "Machine", "id": 17, "field": "status", "before": "MAINTENANCE", "after": "READY",
    }  # fmt: skip

    res = post(client, "restartMachine", "mgr1", {"machineId": 17})
    assert res.status_code == 200
    assert res.json()["actionId"] == 5


@pytest.mark.parametrize(
    ("headers", "content", "status", "code"),
    [
        ({}, b'{"machineId": 17}', 401, "UNKNOWN_USER"),
        ({"X-User-Id": ""}, b'{"machineId": 17}', 401, "UNKNOWN_USER"),
        ({"X-User-Id": "mgr1"}, b"not json", 422, "INVALID_INPUT"),
        ({"X-User-Id": "mgr1"}, b"", 422, "INVALID_INPUT"),
        ({"X-User-Id": "mgr1"}, b"[17]", 422, "INVALID_INPUT"),
    ],
)
def test_bad_requests_are_audited(client, store, headers, content, status, code):
    res = client.post(
        "/actions/restartMachine",
        content=content,
        headers={"Content-Type": "application/json", **headers},
    )
    assert res.status_code == status
    assert res.json()["reasonCode"] == code
    assert res.json()["actionId"] == 1
    assert len(store.audit) == 1


def test_unknown_action_is_404_with_common_shape(client):
    res = post(client, "deleteMachine", "mgr1", {"machineId": 17})
    assert res.status_code == 404
    assert res.json()["reasonCode"] == "NOT_FOUND"


def test_unknown_route_uses_common_shape(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert list(res.json()) == ["result", "stage", "reasonCode", "message", "violations"]


# ---------------------------------------------------------------------------
# GET /audit
# ---------------------------------------------------------------------------


def test_audit_list_and_filters(client):
    post(client, "restartMachine", "fw1", {"machineId": 17})  # 1 DENIED
    post(client, "restartMachine", "mgr1", {"machineId": 17})  # 2 DENIED
    post(client, "requestInspection", "fw1", {"machineId": 49, "reason": "소음"})  # 3 ALLOWED

    res = client.get("/audit", headers={"X-User-Id": "mgr1"})
    assert res.status_code == 200
    assert res.json()["total"] == 3

    res = client.get(
        "/audit", params={"targetType": "Machine", "targetId": 17}, headers={"X-User-Id": "mgr1"}
    )
    items = res.json()["items"]
    assert [i["actionId"] for i in items] == [1, 2]
    first = items[0]
    assert first == {
        "actionId": 1,
        "action": "restartMachine",
        "result": "DENIED",
        "stage": "PERMISSION",
        "reasonCode": "ROLE_NOT_ALLOWED",
        "actor": {"userId": "fw1", "role": "FIELD_WORKER"},
        "target": {"type": "Machine", "id": 17},
        "input": {"machineId": 17},
        "response": first["response"],
        "at": "2016-01-01T06:00:00",
        "recordedAt": "2026-11-24T10:00:00",
    }
    assert first["response"]["reasonCode"] == "ROLE_NOT_ALLOWED"

    res = client.get("/audit", params={"result": "ALLOWED"}, headers={"X-User-Id": "mgr1"})
    assert [i["action"] for i in res.json()["items"]] == ["requestInspection"]
    res = client.get("/audit", params={"actorId": "fw1"}, headers={"X-User-Id": "mgr1"})
    assert res.json()["total"] == 2


@pytest.mark.parametrize(
    "params",
    [{"targetId": "abc"}, {"result": "denied"}, {"machineId": "17"}],
)
def test_audit_invalid_filter(client, params):
    res = client.get("/audit", params=params, headers={"X-User-Id": "mgr1"})
    assert res.status_code == 422
    assert res.json()["reasonCode"] == "INVALID_FILTER"


def test_audit_needs_user(client):
    assert client.get("/audit").status_code == 401
    assert client.get("/audit", headers={"X-User-Id": "ghost"}).status_code == 401


def test_openapi_lists_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/actions/{action_name}" in paths
    assert "/audit" in paths
