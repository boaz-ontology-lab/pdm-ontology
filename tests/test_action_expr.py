from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from app.action.expr import ExprError, compile_expr, compile_template

# ---------------------------------------------------------------------------
# 테스트용 Resolver: 객체를 메모리에 두고, 링크는 외래 키 하나로 잇는다
# ---------------------------------------------------------------------------

# 링크 이름: (from 타입, to 타입, 외래 키를 가진 쪽, 외래 키)
# 역방향 링크(hasWorkOrders)도 links.yaml의 inverse 이름처럼 이름 하나로 부른다
LINKS = {
    "hasFailures": ("Machine", "FailureEvent", "to", "machineId"),
    "targets": ("WorkOrder", "Machine", "from", "machineId"),
    "hasWorkOrders": ("Machine", "WorkOrder", "to", "machineId"),
}
PK = {"Machine": "machineId", "FailureEvent": "failureId", "WorkOrder": "workOrderId"}


@dataclass(eq=False)
class Obj:
    type: str
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> Any:
        return self.props[PK[self.type]]


class FakeResolver:
    def __init__(self, objects: list[Obj]) -> None:
        self.objects = objects

    def has_type(self, type_name: str) -> bool:
        return type_name in PK

    def get(self, type_name: str, object_id: Any) -> Obj | None:
        return next((o for o in self._all(type_name) if o.id == object_id), None)

    def attr(self, obj: Obj, name: str) -> Any:
        if name in obj.props:
            return obj.props[name]
        if name in LINKS and LINKS[name][0] == obj.type:
            _, to_type, fk_side, fk = LINKS[name]
            if fk_side == "to":
                return [o for o in self._all(to_type) if o.props[fk] == obj.id]
            return self.get(to_type, obj.props[fk])
        raise ExprError(f"{obj.type}에 '{name}'이 없습니다")

    def _all(self, type_name: str) -> list[Obj]:
        return [o for o in self.objects if o.type == type_name]


M17 = Obj("Machine", {"machineId": 17, "model": "model1", "status": "DOWN", "lastRestartAt": None})
M49 = Obj(
    "Machine", {"machineId": 49, "model": "model2", "status": "RUNNING", "lastRestartAt": None}
)
F121 = Obj(
    "FailureEvent", {"failureId": 121, "machineId": 17, "comp": "comp2", "isResolved": False}
)
F120 = Obj("FailureEvent", {"failureId": 120, "machineId": 17, "comp": "comp1", "isResolved": True})
W1 = Obj("WorkOrder", {"workOrderId": 1, "machineId": 17, "comp": "comp2", "status": "OPEN"})
W2 = Obj("WorkOrder", {"workOrderId": 2, "machineId": 17, "comp": "comp1", "status": "DONE"})
RESOLVER = FakeResolver([M17, M49, F120, F121, W1, W2])
NOW = datetime(2016, 1, 1, 6, 0)


def run(source: str, target: Any = M17, **inputs: Any) -> Any:
    names = {"target": target, "actor": None, "input": inputs, "now": NOW}
    return compile_expr(source).evaluate(RESOLVER, names)


# ---------------------------------------------------------------------------
# 평가
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("target.status == DOWN", True),
        ("target.status != RUNNING and target.model in ['model1', 'model2']", True),
        ("not target.status == DOWN or false", False),
        ("target.lastRestartAt == null", True),
        ("target.lastRestartAt.year", None),  # null의 속성은 null
        ("input.machineId", 17),
        ("input.missing", None),
        ("'A' if target.status == DOWN else 'B'", "A"),
        ("1 < 2 <= 2", True),
    ],
)
def test_basic(source, expected):
    assert run(source, machineId=17) == expected


def test_object_lookup():
    assert run("Machine(49).status") == "RUNNING"
    assert run("Machine(999)") is None
    assert run("Machine(999).status") is None
    assert run("Machine(input.id)", id=None) is None


def test_links_and_comprehension():
    assert run("[f.failureId for f in target.hasFailures]") == [120, 121]
    assert run("[f for f in target.hasFailures if not f.isResolved]") == [F121]
    assert run("count(f for f in target.hasFailures if not f.isResolved)") == 1
    assert run("any(f.comp == input.comp for f in target.hasFailures)", comp="comp2") is True
    assert run("none(f.comp == 'comp3' for f in target.hasFailures)") is True
    assert run("first(f.failureId for f in target.hasFailures if f.comp == 'comp9')") is None
    assert run("Machine(17).hasFailures", target=M49) == [F120, F121]


def test_nested_comprehension():
    source = (
        "[w.workOrderId for w in target.hasWorkOrders"
        " for f in target.hasFailures if w.comp == f.comp]"
    )
    assert run(source) == [1, 2]


def test_many_to_one_and_reverse_link():
    assert run("target.targets.machineId", target=W1) == 17
    assert run("[w.workOrderId for w in target.hasWorkOrders if w.status == OPEN]") == [1]
    assert run("target.hasWorkOrders", target=M49) == []
    assert run("Machine(999).hasWorkOrders") is None
    assert run("[w for w in Machine(999).hasWorkOrders]") == []


def test_coalesce():
    assert run("coalesce(input.failureId, 5)") == 5
    assert run("coalesce(input.failureId, 5)", failureId=121) == 121
    assert run("coalesce(null, null)") is None
    # 앞의 값이 있으면 뒤는 평가하지 않는다 (뒤 식은 평가하면 오류)
    assert run("coalesce(1, Machine(17).nope)") == 1


def test_generator_result_is_materialized():
    assert run("(f.failureId for f in target.hasFailures)") == [120, 121]


def test_compile_is_cached():
    assert compile_expr("target.status == DOWN") is compile_expr("target.status == DOWN")


# ---------------------------------------------------------------------------
# 거부되는 식
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "open('x')",
        "target.__class__",
        "target._private",
        "lambda: 1",
        "target[0]",
        "1 + 1",
        "None",
        "True",
        "target is null",
        "machine",  # 모르는 이름
        "Machine",  # 부르지 않은 객체 타입
        "count(target.hasFailures, 1)",
        "count(x=target.hasFailures)",
        "coalesce()",
        "target.inverse(targets)",  # 역방향은 links.yaml의 inverse 이름으로 부른다
        "target.status.upper()",
        "[target for target in target.hasFailures]",
        "{'a': 1}",
        "x = 1",
        "target.status ==",
    ],
)
def test_rejected_at_compile(source):
    with pytest.raises(ExprError):
        compile_expr(source)


@pytest.mark.parametrize(
    "source",
    [
        "target.lastRestartAt < now",  # null과 크기 비교
        "Vendor(1)",  # Resolver가 모르는 객체 타입
        "target.nope",  # 없는 속성
        "count(target.status)",  # 문자열은 목록이 아니다
        "[f for f in f.hasFailures]",  # 범위 밖 변수
        "target.status.foo",  # 문자열 값의 속성
        "input.machineId.foo",  # 숫자 값의 속성
        "now.year",  # 날짜 값의 속성
        "target.hasFailures.count",  # 목록 값의 속성
    ],
)
def test_rejected_at_evaluation(source):
    with pytest.raises(ExprError) as e:
        run(source, machineId=17)
    assert source in str(e.value)


# ---------------------------------------------------------------------------
# 메시지 템플릿
# ---------------------------------------------------------------------------


def test_template():
    names = {"target": M17, "actor": None, "input": {}, "now": NOW}
    template = compile_template(
        "미해결 고장 {count(f for f in target.hasFailures if not f.isResolved)}건,"
        " 상태 {target.status}"
    )
    assert template.render(RESOLVER, names) == "미해결 고장 1건, 상태 DOWN"
    assert compile_template("값 {target.lastRestartAt}").render(RESOLVER, names) == "값 null"
    assert compile_template("자리표시 없음").render(RESOLVER, names) == "자리표시 없음"


def test_template_rejects_bad_expression():
    with pytest.raises(ExprError):
        compile_template("{__import__('os')}")
