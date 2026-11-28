"""필터, 정렬, 페이지를 읽고 SQL로 바꾸는 부분. DB 없이 돈다."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.engine import query as q
from app.engine import spec
from app.engine.query import Condition, QueryError, SortKey


@pytest.fixture(scope="module")
def ontology():
    return spec.load()


@pytest.fixture(scope="module")
def machine(ontology):
    return ontology.object("Machine")


def _error(obj, params) -> QueryError:
    with pytest.raises(QueryError) as e:
        q.parse_list_query(obj, params)
    return e.value


def test_defaults(machine):
    lq = q.parse_list_query(machine, {})
    assert (lq.filters, lq.sort, lq.limit, lq.offset) == ((), (), 50, 0)


def test_filters_are_typed(ontology, machine):
    lq = q.parse_list_query(
        machine, {"age__gte": "10", "model__in": "model1,model2", "limit": "20", "offset": "40"}
    )
    assert lq.filters == (
        Condition("age", "gte", 10),
        Condition("model", "in", ["model1", "model2"]),
    )
    assert (lq.limit, lq.offset) == (20, 40)

    failures = ontology.object("FailureEvent")
    lq = q.parse_list_query(failures, {"datetime__lte": "2015-12-31T06:00:00"})
    assert lq.filters == (Condition("datetime", "lte", datetime(2015, 12, 31, 6)),)

    work_orders = ontology.object("WorkOrder")
    lq = q.parse_list_query(work_orders, {"status": "OPEN"})
    assert lq.filters == (Condition("status", "eq", "OPEN"),)


def test_sort(machine):
    lq = q.parse_list_query(machine, {"sort": "-age,model"})
    assert lq.sort == (SortKey("age", True), SortKey("model", False))


@pytest.mark.parametrize(
    "params, text",
    [
        ({"color": "red"}, "속성 color이 없습니다"),
        ({"age__between": "1"}, "연산자 '__between'"),
        ({"age__eq": "1"}, "연산자 '__eq'"),
        ({"age__": "1"}, "연산자 '__'"),
        ({"model__gte": "model2"}, "__gte를 쓸 수 없습니다"),
        ({"age": "old"}, "int로 읽을 수 없습니다"),
        ({"model__in": ","}, "값이 없습니다"),
        ({"sort": "-color"}, "속성 color이 없습니다"),
        ({"riskLevel": "HIGH"}, "파생 속성 riskLevel"),
        ({"sort": "-errorCount7d"}, "파생 속성 errorCount7d"),
    ],
)
def test_invalid_filter(machine, params, text):
    e = _error(machine, params)
    assert (e.code, e.status) == ("INVALID_FILTER", 422)
    assert text in e.message


def test_invalid_enum_and_datetime(ontology):
    e = _error(ontology.object("WorkOrder"), {"status": "open"})
    assert e.code == "INVALID_FILTER" and "OPEN, DONE, CANCELED" in e.message
    e = _error(ontology.object("FailureEvent"), {"datetime__gte": "2015-12-01T00:00:00+09:00"})
    assert e.code == "INVALID_FILTER"
    e = _error(ontology.object("InspectionRequest"), {"evidence": "{}"})
    assert e.code == "INVALID_FILTER" and "json" in e.message


def test_all_problems_are_reported(machine):
    e = _error(machine, {"color": "red", "age": "old", "sort": "x"})
    assert [v["detail"]["field"] for v in e.violations] == ["color", "age", "sort"]
    assert {v["code"] for v in e.violations} == {"INVALID_FILTER"}


def test_page_problems_are_reported_with_filter_problems(machine):
    """limit, offset 오류도 필터 오류와 함께 모은다. reasonCode는 첫 번째 코드다."""
    e = _error(machine, {"nope": "1", "limit": "9999", "offset": "-1"})
    assert [(v["code"], v["detail"]["field"]) for v in e.violations] == [
        ("INVALID_FILTER", "nope"),
        ("INVALID_INPUT", "limit"),
        ("INVALID_INPUT", "offset"),
    ]
    assert e.code == "INVALID_FILTER" and e.status == 422

    page = _error(machine, {"limit": "0", "offset": "-1"})
    assert page.code == "INVALID_INPUT"
    assert [v["detail"]["field"] for v in page.violations] == ["limit", "offset"]


def test_error_body_matches_api_doc(machine):
    """docs/api.md 1절 "실패 응답 모양". actionId는 액션 요청에만 있다."""
    body = _error(machine, {"color": "red"}).to_body()
    assert body == {
        "result": "ERROR",
        "stage": "INPUT",
        "reasonCode": "INVALID_FILTER",
        "message": "Machine에 속성 color이 없습니다",
        "violations": [
            {
                "code": "INVALID_FILTER",
                "detail": {
                    "field": "color",
                    "problem": "Machine에 속성 color이 없습니다",
                    "value": "red",
                },
            }
        ],
    }
    page = _error(machine, {"limit": "0"}).to_body()
    assert page["reasonCode"] == "INVALID_INPUT"
    assert page["violations"] == [
        {
            "code": "INVALID_INPUT",
            "detail": {
                "field": "limit",
                "problem": "limit은 1~500의 정수여야 합니다 ('0')",
                "value": "0",
            },
        }
    ]


@pytest.mark.parametrize(
    "params", [{"limit": "0"}, {"limit": "501"}, {"limit": "ten"}, {"offset": "-1"}]
)
def test_invalid_page(machine, params):
    assert _error(machine, params).code == "INVALID_INPUT"


def test_list_sql_binds_values(machine):
    lq = q.parse_list_query(machine, {"model": "model1' OR '1'='1", "sort": "-age", "limit": "5"})
    st = q.select_list(machine, lq)
    text = st.sql.as_string()
    assert "OR '1'='1" not in text
    assert st.params == ("model1' OR '1'='1", 5, 0)
    assert 'FROM "core"."machine" "t" WHERE "t"."model" = %s' in text
    # 정렬 뒤에 primary key가 붙어 같은 나이끼리도 순서가 정해진다
    assert text.endswith('ORDER BY "t"."age" DESC, "t"."machine_id" ASC LIMIT %s OFFSET %s')
    assert '"t"."vendor_id" AS "vendorId"' in text


def test_count_sql_has_same_filters(machine):
    lq = q.parse_list_query(machine, {"model__in": "model1,model2", "limit": "5"})
    st = q.count_list(machine, lq)
    assert st.sql.as_string() == (
        'SELECT count(*) FROM "core"."machine" "t" WHERE "t"."model" = ANY(%s)'
    )
    assert st.params == (["model1", "model2"],)


def test_primary_key_sort_is_not_repeated(machine):
    lq = q.parse_list_query(machine, {"sort": "-machineId"})
    assert q.select_list(machine, lq).sql.as_string().count('machine_id" DESC') == 1
    assert 'machine_id" ASC' not in q.select_list(machine, lq).sql.as_string()


# ---------------------------------------------------------------------------
# 링크 조건


def _scope(ontology, type_name, link_name, values):
    link = ontology.link(type_name, link_name)
    original = (link.to_type, link.from_type) if link.inverse_of else (link.from_type, link.to_type)
    ends = {"from": ontology.object(original[0]), "to": ontology.object(original[1])}
    clause = q.link_scope(link, ends, values)
    return clause.sql.as_string(None), clause.params


def test_link_scope_join(ontology):
    text, params = _scope(ontology, "Machine", "hasFailures", {"machineId": 17})
    assert (text, params) == ('(%s = "t"."machine_id")', (17,))
    # 역방향은 같은 join을 반대로 채운다
    text, params = _scope(ontology, "FailureEvent", "failedOn", {"failureId": 121, "machineId": 17})
    assert (text, params) == ('("t"."machine_id" = %s)', (17,))


def test_link_scope_pick(ontology):
    """placeholder 순서와 params 순서가 맞아야 한다. 고른 행은 primary key로 비교한다."""
    values = {"failureId": 121, "machineId": 17, "comp": "comp2", "datetime": "D"}
    text, params = _scope(ontology, "FailureEvent", "resolvedBy", values)
    assert text.count("%s") == len(params) == 6
    assert params == (17, "comp2", "D", 17, "comp2", "D")
    assert '"t"."maint_id" = (SELECT "p"."maint_id"' in text
    assert 'ORDER BY "p"."datetime" ASC, "p"."maint_id" LIMIT 1' in text

    values = {"maintId": 510, "machineId": 3, "comp": "comp1", "datetime": "D"}
    text, params = _scope(ontology, "Maintenance", "resolves", values)
    assert text.count("%s") == len(params) == 4
    assert params == (3, "comp1", "D", 510)
    assert '%s = (SELECT "p"."maint_id"' in text
    assert '"t"."machine_id" = "p"."machine_id"' in text  # 하위 질의는 t(고장)에서 고른다


def test_link_scope_through(ontology):
    text, params = _scope(ontology, "ErrorEvent", "indicates", {"errorType": "error1"})
    assert text == (
        '(EXISTS (SELECT 1 FROM "ref"."error_component_map" "via"'
        ' WHERE "via"."error_type" = %s AND "via"."comp" = "t"."comp"))'
    )
    assert params == ("error1",)


def test_select_list_puts_scope_before_filters(ontology):
    failures = ontology.object("FailureEvent")
    scope = q.Clause(q.sql.SQL("x = %s"), ("scope",))
    lq = q.parse_list_query(failures, {"comp": "comp2"})
    st = q.select_list(failures, lq, scope)
    assert ' WHERE x = %s AND "t"."comp" = %s ORDER BY' in st.sql.as_string(None)
    assert st.params == ("scope", "comp2", 50, 0)
    assert q.select_list(failures, q.ALL).params[-2:] == (None, 0)  # LIMIT NULL은 전부다
