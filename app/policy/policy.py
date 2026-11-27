"""조회 정책: 역할별 행 필터와 컬럼 가림 (#19, 결정 22).

규칙은 ontology/policies.yaml, 대상은 ontology/policy_tags.yaml(OpenMetadata 태그 스냅샷)에 있다.
둘을 합쳐 "이 역할은 이 테이블에서 어떤 행만 보고, 어떤 컬럼을 못 보는가"를 테이블 단위로 돌려준다.

- 조회 엔진(B): RolePolicy.row_rules(table)의 RowRule.sql()을 WHERE에 더하고,
  masked_columns(table)의 값은 null로 바꿔 masked에 담는다
- 액션 엔진(C): Visibility.allows()로 대상 객체가 보이는지 본다. 보이지 않으면 404
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from psycopg import sql

ONTOLOGY = Path(__file__).resolve().parents[2] / "ontology"
ACTOR_ATTR = re.compile(r"^actor\.([a-z][a-zA-Z0-9]*)$")


class PolicyError(Exception):
    """policies.yaml, policy_tags.yaml, objects.yaml이 서로 맞지 않는다."""


@dataclass(frozen=True)
class RowRule:
    name: str
    description: str
    column: str
    lookup: str
    key: str
    where: tuple[tuple[str, str], ...]  # (lookup 테이블 컬럼, actor 속성)

    def sql(self, alias: str, actor: Mapping[str, Any]) -> tuple[sql.Composed, tuple[Any, ...]]:
        """WHERE에 더할 조건과 파라미터. alias는 거를 테이블의 별칭이다.

        예: "f"."machine_id" IN (SELECT "machine_id" FROM "core"."machine" WHERE "vendor_id" = %s)
        """
        conditions = sql.SQL(" AND ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column, _ in self.where
        )
        query = sql.SQL("{} IN (SELECT {} FROM {} WHERE {})").format(
            sql.Identifier(alias, self.column),
            sql.Identifier(self.key),
            sql.Identifier(*self.lookup.split(".")),
            conditions,
        )
        return query, tuple(actor.get(attr) for _, attr in self.where)


@dataclass(frozen=True)
class RolePolicy:
    role: str
    rules_by_table: Mapping[str, tuple[RowRule, ...]] = field(default_factory=dict)
    masked_by_table: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @property
    def restricted(self) -> bool:
        return bool(self.rules_by_table or self.masked_by_table)

    def row_rules(self, table: str) -> tuple[RowRule, ...]:
        return self.rules_by_table.get(table, ())

    def masked_columns(self, table: str) -> frozenset[str]:
        return self.masked_by_table.get(table, frozenset())

    def explain(self, table: str) -> dict[str, Any]:
        """explain 응답의 policy. 걸린 것이 없으면 빈 dict."""
        out: dict[str, Any] = {}
        if rules := self.row_rules(table):
            out["rowFilter"] = [{"rule": r.name, "description": r.description} for r in rules]
        if masked := self.masked_columns(table):
            out["maskedColumns"] = sorted(masked)
        return out


@dataclass(frozen=True)
class Policy:
    roles: Mapping[str, RolePolicy]
    synced_from: str

    def for_role(self, role: str | None) -> RolePolicy:
        """여기 없는 역할은 제한이 없다."""
        return self.roles.get(role or "", RolePolicy(role or ""))


def load_policy(
    policies_path: Path = ONTOLOGY / "policies.yaml",
    tags_path: Path = ONTOLOGY / "policy_tags.yaml",
) -> Policy:
    return parse_policy(_read(policies_path), _read(tags_path))


def parse_policy(policies: Mapping[str, Any], tags: Mapping[str, Any]) -> Policy:
    tag_tables, tag_columns = _parse_tags(tags.get("tags") or {})
    rules = {name: _parse_rule(name, body) for name, body in (policies.get("rules") or {}).items()}

    roles: dict[str, RolePolicy] = {}
    for role, body in (policies.get("roles") or {}).items():
        by_table: dict[str, list[RowRule]] = {}
        for item in body.get("row_filter") or []:
            tag, rule_name = item["tag"], item["rule"]
            if rule_name not in rules:
                raise PolicyError(f"{role}.row_filter: 없는 규칙 {rule_name}")
            if tag in tag_columns:
                raise PolicyError(
                    f"{role}.row_filter: {tag}는 컬럼에 붙는 태그라 행을 거를 수 없다"
                )
            for table in tag_tables.get(tag, ()):
                by_table.setdefault(table, []).append(rules[rule_name])
        masked: dict[str, set[str]] = {}
        for item in body.get("mask") or []:
            tag = item["tag"]
            if tag in tag_tables:
                raise PolicyError(f"{role}.mask: {tag}는 테이블에 붙는 태그라 컬럼을 가릴 수 없다")
            for table, column in tag_columns.get(tag, ()):
                masked.setdefault(table, set()).add(column)
        roles[role] = RolePolicy(
            role,
            {t: tuple(rs) for t, rs in by_table.items()},
            {t: frozenset(cs) for t, cs in masked.items()},
        )
    return Policy(roles, str(tags.get("synced_from", "manual")))


def _parse_tags(
    tags: Mapping[str, Any],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[tuple[str, str], ...]]]:
    tables: dict[str, tuple[str, ...]] = {}
    columns: dict[str, tuple[tuple[str, str], ...]] = {}
    for tag, body in tags.items():
        if body.get("tables"):
            tables[tag] = tuple(body["tables"])
        if body.get("columns"):
            parsed = []
            for full in body["columns"]:
                schema, table, column = full.split(".")
                parsed.append((f"{schema}.{table}", column))
            columns[tag] = tuple(parsed)
    return tables, columns


def _parse_rule(name: str, body: Mapping[str, Any]) -> RowRule:
    where = []
    for column, value in (body.get("where") or {}).items():
        m = ACTOR_ATTR.match(str(value))
        if not m:
            raise PolicyError(f"rules.{name}.where: '{value}'는 actor.속성 형식이어야 한다")
        where.append((column, m.group(1)))
    if not where:
        raise PolicyError(f"rules.{name}: where가 비었다. 모든 행이 보이게 된다")
    return RowRule(
        name, body["description"], body["column"], body["lookup"], body["key"], tuple(where)
    )


def _read(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# 액션 엔진: 대상 객체가 보이는가
# ---------------------------------------------------------------------------


class Visibility:
    """객체 단위로 행 필터를 판정한다.

    테이블과 컬럼은 objects.yaml의 source, column으로 객체에 잇는다.
    """

    def __init__(self, policy: Policy, objects: Mapping[str, Any]) -> None:
        self.policy = policy
        self._source = {name: body["source"] for name, body in objects.items()}
        self._type_of = {body["source"]: name for name, body in objects.items()}
        self._prop = {
            name: {p["column"]: prop for prop, p in (body.get("properties") or {}).items()}
            for name, body in objects.items()
        }
        self._pk = {name: body["primary_key"] for name, body in objects.items()}
        self._check()

    def _check(self) -> None:
        """규칙이 걸린 객체마다 컬럼과 lookup이 objects.yaml에 있는지 미리 본다."""
        for role in self.policy.roles.values():
            for table, rules in role.rules_by_table.items():
                obj = self._type_of.get(table)
                if obj is None:
                    continue  # 객체가 아닌 테이블(raw.telemetry 시계열)은 조회 엔진만 거른다
                for rule in rules:
                    self._column_prop(obj, rule.column, rule.name)
                    lookup = self._type_of.get(rule.lookup)
                    if lookup is None:
                        raise PolicyError(
                            f"{rule.name}: lookup {rule.lookup}은 객체의 source가 아니다"
                        )
                    if self._column_prop(lookup, rule.key, rule.name) != self._pk[lookup]:
                        raise PolicyError(
                            f"{rule.name}: key {rule.key}는 {lookup}의 primary key여야 한다"
                        )
                    for column, _ in rule.where:
                        self._column_prop(lookup, column, rule.name)

    def _column_prop(self, type_name: str, column: str, rule: str) -> str:
        prop = self._prop[type_name].get(column)
        if prop is None:
            raise PolicyError(f"{rule}: {type_name}에 컬럼 {column}이 없다")
        return prop

    def allows(self, store: Any, actor: Any, type_name: str, obj: Any) -> bool:
        """actor가 obj를 볼 수 있는가. store는 Resolver(get, attr)면 된다."""
        role = self.policy.for_role(store.attr(actor, "role"))
        for rule in role.row_rules(self._source[type_name]):
            value = store.attr(obj, self._prop[type_name][rule.column])
            lookup = self._type_of[rule.lookup]
            row = store.get(lookup, value) if value is not None else None
            if row is None:
                return False
            for column, attr in rule.where:
                if store.attr(row, self._prop[lookup][column]) != store.attr(actor, attr):
                    return False
        return True


def load_visibility(policy: Policy | None = None) -> Visibility:
    return Visibility(policy or load_policy(), _read(ONTOLOGY / "objects.yaml"))
