"""액션 엔진 테스트용 메모리 저장소.

DB와 조회 엔진(B)이 준비되기 전까지 엔진을 검증하려고 둔다.
파생 속성은 docs/decisions.md 핵심 규칙(resolvedBy, Machine.status)을 그대로 옮겼다.
errorCount7d, riskLevel은 이 테스트에서 계산하지 않고 fixture 값을 그대로 쓴다.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.action.expr import ExprError
from app.action.store import AuditEntry, AuditRecord, StoreConflict

PK = {
    "Machine": "machineId",
    "FailureEvent": "failureId",
    "Maintenance": "maintId",
    "WorkOrder": "workOrderId",
    "InspectionRequest": "inspectionRequestId",
    "User": "userId",
    "Component": "comp",
}
# 액션이 만드는 객체의 첫 번호 (교체 기록은 원본 뒤로 3287부터)
FIRST_ID = {"WorkOrder": 1, "InspectionRequest": 1, "Maintenance": 3287}


class ConstraintViolation(Exception):
    """CHECK, NOT NULL 제약 위반. DB에서는 IntegrityError로 500이 된다."""


@dataclass(eq=False)
class Obj:
    type: str
    props: dict[str, Any]

    @property
    def id(self) -> Any:
        return self.props[PK[self.type]]

    def __repr__(self) -> str:
        return f"{self.type}({self.id})"


@dataclass
class MemoryStore:
    objects: dict[str, list[Obj]] = field(default_factory=lambda: {t: [] for t in PK})
    next_id: dict[str, int] = field(default_factory=lambda: dict(FIRST_ID))
    audit: list[AuditRecord] = field(default_factory=list)
    recorded_at: list[datetime] = field(default_factory=list)
    # 감사 로그의 실제 기록 시각. DB에서는 now() 기본값이고, 테스트에서는 고정한다
    real_clock: Callable[[], datetime] = field(default=lambda: datetime(2026, 11, 24, 10, 0))

    # ---- 데이터 넣기 (fixture) -------------------------------------------

    def add(self, type_name: str, **props: Any) -> Obj:
        obj = Obj(type_name, props)
        self.objects[type_name].append(obj)
        return obj

    def all(self, type_name: str) -> list[Obj]:
        return sorted(self.objects[type_name], key=lambda o: o.id)

    # ---- Resolver ----------------------------------------------------------

    def has_type(self, type_name: str) -> bool:
        return type_name in PK

    def get(self, type_name: str, object_id: Any) -> Obj | None:
        return next((o for o in self.objects[type_name] if o.id == object_id), None)

    def attr(self, obj: Obj, name: str) -> Any:
        if name in obj.props:
            return obj.props[name]
        compute = COMPUTED.get((obj.type, name))
        if compute is None:
            raise ExprError(f"{obj.type}에 '{name}'이 없습니다")
        return compute(self, obj)

    # ---- Store -------------------------------------------------------------

    def is_object(self, value: Any) -> bool:
        return isinstance(value, Obj)

    def ref(self, obj: Obj) -> tuple[str, Any]:
        return obj.type, obj.id

    def properties(self, obj: Obj) -> dict[str, Any]:
        return dict(obj.props)

    def create(self, type_name: str, values: Mapping[str, Any]) -> Obj:
        props = dict(values)
        props[PK[type_name]] = self.next_id[type_name]
        self.next_id[type_name] += 1
        if type_name == "Maintenance":
            props["source"] = "ACTION"  # core.maintenance 뷰가 붙이는 값
        obj = Obj(type_name, props)
        self._check_constraints(obj)
        self.objects[type_name].append(obj)
        return obj

    def update(self, obj: Obj, values: Mapping[str, Any]) -> None:
        for field_name in values:
            if field_name not in obj.props:
                raise ExprError(f"{obj.type}.{field_name}은 저장 속성이 아닙니다")
        obj.props.update(values)
        self._check_constraints(obj)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        saved = {t: [(o, copy.deepcopy(o.props)) for o in objs] for t, objs in self.objects.items()}
        saved_ids = dict(self.next_id)
        try:
            yield
        except BaseException:
            for type_name, items in saved.items():
                for obj, props in items:
                    obj.props = props
                self.objects[type_name] = [obj for obj, _ in items]
            self.next_id = saved_ids
            raise

    def append_audit(self, record: AuditRecord) -> int:
        self.audit.append(record)
        self.recorded_at.append(self.real_clock())
        return len(self.audit)

    def find_audit(self, filters: Mapping[str, Any]) -> list[AuditEntry]:
        return [
            AuditEntry(i, record, at)
            for i, (record, at) in enumerate(zip(self.audit, self.recorded_at, strict=True), 1)
            if all(getattr(record, k) == v for k, v in filters.items())
        ]

    def _check_constraints(self, obj: Obj) -> None:
        """db/05_ops.sql의 제약. CHECK, NOT NULL 위반은 엔진 버그라 StoreConflict가 아니다."""
        p = obj.props
        if obj.type == "WorkOrder":
            if (p["status"] == "DONE") != (p["completedAt"] is not None):
                raise ConstraintViolation("work_orders: DONE일 때만 completed_at이 있다")
            if (p["type"] == "CORRECTIVE") != (p["failureId"] is not None):
                raise ConstraintViolation("work_orders: CORRECTIVE일 때만 failure_id가 있다")
        restart = ("lastRestartAt", "lastRestartBy", "lastRestartWorkOrderId")
        if obj.type == "Machine" and len({p[k] is None for k in restart}) > 1:
            raise ConstraintViolation("machine_state: 재가동 값 세 개는 함께 있다")
        if obj.type == "WorkOrder" and obj.props["status"] == "OPEN":
            if any(
                w is not obj
                and w.props["status"] == "OPEN"
                and (w.props["machineId"], w.props["comp"])
                == (obj.props["machineId"], obj.props["comp"])
                for w in self.objects["WorkOrder"]
            ):
                raise StoreConflict("OPEN_WORK_ORDER")
        if obj.type == "InspectionRequest" and obj.props["status"] == "REQUESTED":
            if any(
                r is not obj
                and r.props["status"] == "REQUESTED"
                and r.props["machineId"] == obj.props["machineId"]
                for r in self.objects["InspectionRequest"]
            ):
                raise StoreConflict("DUPLICATE_REQUEST")


# ---------------------------------------------------------------------------
# 링크와 파생 속성
# ---------------------------------------------------------------------------


def _children(type_name: str, fk: str, pk: str) -> Callable[[MemoryStore, Obj], list[Obj]]:
    return lambda s, o: [c for c in s.all(type_name) if c.props.get(fk) == o.props[pk]]


def _parent(type_name: str, fk: str) -> Callable[[MemoryStore, Obj], Obj | None]:
    return lambda s, o: s.get(type_name, o.props.get(fk))


def _resolved_by(s: MemoryStore, f: Obj) -> Obj | None:
    """같은 설비, 같은 부품이고 고장 시각 이후인 교체 기록 중 가장 이른 것."""
    candidates = [
        m
        for m in s.all("Maintenance")
        if m.props["machineId"] == f.props["machineId"]
        and m.props["comp"] == f.props["comp"]
        and m.props["datetime"] >= f.props["datetime"]
    ]
    return min(candidates, key=lambda m: m.props["datetime"], default=None)


def _open_work_orders(s: MemoryStore, m: Obj) -> list[Obj]:
    return [w for w in s.attr(m, "hasWorkOrders") if w.props["status"] == "OPEN"]


def _unresolved(s: MemoryStore, m: Obj) -> list[Obj]:
    return [f for f in s.attr(m, "hasFailures") if not s.attr(f, "isResolved")]


def _has_uncovered_failure(s: MemoryStore, m: Obj) -> bool:
    covered = {w.props["failureId"] for w in _open_work_orders(s, m)}
    return any(f.id not in covered for f in _unresolved(s, m))


def _status(s: MemoryStore, m: Obj) -> str:
    if _has_uncovered_failure(s, m):
        return "DOWN"
    if _open_work_orders(s, m):
        return "MAINTENANCE"
    # 재가동 이후에 끝난 작업지시는 시각이 아니라 번호로 가린다 (결정 21)
    last = m.props["lastRestartWorkOrderId"]
    done_after_restart = any(
        w.props["status"] == "DONE" and (last is None or w.id > last)
        for w in s.attr(m, "hasWorkOrders")
    )
    return "READY" if done_after_restart else "RUNNING"


COMPUTED: dict[tuple[str, str], Callable[[MemoryStore, Obj], Any]] = {
    # 링크 (역방향은 links.yaml의 inverse 이름)
    ("Machine", "hasFailures"): _children("FailureEvent", "machineId", "machineId"),
    ("Machine", "hasMaintenance"): _children("Maintenance", "machineId", "machineId"),
    ("Machine", "hasWorkOrders"): _children("WorkOrder", "machineId", "machineId"),
    ("Machine", "hasInspectionRequests"): _children("InspectionRequest", "machineId", "machineId"),
    ("WorkOrder", "targets"): _parent("Machine", "machineId"),
    ("WorkOrder", "addresses"): _parent("FailureEvent", "failureId"),
    ("InspectionRequest", "concerns"): _parent("Machine", "machineId"),
    ("FailureEvent", "addressedBy"): _children("WorkOrder", "failureId", "failureId"),
    ("FailureEvent", "resolvedBy"): _resolved_by,
    # 파생 속성
    ("FailureEvent", "isResolved"): lambda s, f: _resolved_by(s, f) is not None,
    ("Machine", "hasOpenFailure"): lambda s, m: bool(_unresolved(s, m)),
    ("Machine", "hasUncoveredFailure"): _has_uncovered_failure,
    ("Machine", "hasOpenWorkOrder"): lambda s, m: bool(_open_work_orders(s, m)),
    ("Machine", "status"): _status,
}


# ---------------------------------------------------------------------------
# 기준 fixture (Notion C 문서의 테스트 fixture)
# ---------------------------------------------------------------------------

NOW = datetime(2016, 1, 1, 6, 0)


def demo_store() -> MemoryStore:
    """설비와 고장 번호는 실제 데이터와 결정 14를 따른다.

    M17  model1(벤더 A). comp2 미해결 고장 121 → DOWN (데모 2번)
    M88  model4(벤더 B). comp2 고장 648, comp4 고장 649 동시 미해결 → DOWN (동시 고장 테스트)
    M49  model2(벤더 A). 예전 고장 300은 교체 기록 2000으로 해결 → RUNNING
    M3   model3(벤더 B). 고장 없음 → RUNNING
    """
    s = MemoryStore()
    for user_id, role, vendor_id in [
        ("fw1", "FIELD_WORKER", None),
        ("fw2", "FIELD_WORKER", None),
        ("mech1", "MECHANIC", None),
        ("mech2", "MECHANIC", None),
        ("mech3", "MECHANIC", None),
        ("mgr1", "MANAGER", None),
        ("venA", "VENDOR", 1),
        ("venB", "VENDOR", 2),
    ]:
        s.add("User", userId=user_id, role=role, vendorId=vendor_id)
    for comp in ["comp1", "comp2", "comp3", "comp4"]:
        s.add("Component", comp=comp)
    for machine_id, model, risk in [
        (17, "model1", "MEDIUM"),
        (88, "model4", "LOW"),
        (49, "model2", "HIGH"),
        (3, "model3", "LOW"),
    ]:
        s.add(
            "Machine",
            machineId=machine_id,
            model=model,
            vendorId=1 if model in ("model1", "model2") else 2,  # 결정 5
            lastRestartAt=None,
            lastRestartBy=None,
            lastRestartWorkOrderId=None,
            errorCount7d=3 if risk == "HIGH" else 0,
            riskLevel=risk,
        )
    for failure_id, machine_id, comp, at in [
        (121, 17, "comp2", datetime(2015, 11, 27, 6)),
        (648, 88, "comp2", datetime(2015, 11, 30, 6)),
        (649, 88, "comp4", datetime(2015, 11, 30, 6)),
        (300, 49, "comp1", datetime(2015, 6, 1, 6)),
    ]:
        s.add("FailureEvent", failureId=failure_id, machineId=machine_id, comp=comp, datetime=at)
    s.add(
        "Maintenance",
        maintId=2000,
        datetime=datetime(2015, 6, 1, 6),
        machineId=49,
        comp="comp1",
        source="RAW",
        workOrderId=None,
    )
    return s
