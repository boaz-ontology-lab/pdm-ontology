"""OpenMetadata의 PdmPolicy 태그와 ontology/policy_tags.yaml을 맞춘다 (#19, #23).

    uv run python scripts/sync_policy_tags.py          # OpenMetadata → 파일 (데모 3)
    uv run python scripts/sync_policy_tags.py --push   # 파일 → OpenMetadata

--push는 분류와 태그를 만들고(있으면 그대로) 파일에 적힌 테이블과 컬럼에 붙인다.
OpenMetadata를 새로 띄운 뒤 make om-ingest 다음에 한 번 실행한다. 떼는 일은 하지 않는다.
"""

import sys
import urllib.error
from pathlib import Path

import yaml
from om_ingest import admin_token, call

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "ontology" / "policy_tags.yaml"
CLASSIFICATION = "PdmPolicy"
DATABASE = "pdm_postgres.pdm"  # infra/openmetadata/postgres.yaml의 serviceName + DB 이름
HEADER = """\
# OpenMetadata 분류 PdmPolicy의 태그가 붙은 테이블과 컬럼 (#19, #23)
# scripts/sync_policy_tags.py가 덮어쓴다. 규칙은 ontology/policies.yaml에 있다.
"""
DESCRIPTIONS = {
    CLASSIFICATION: "조회 정책 대상. 규칙은 ontology/policies.yaml",
    "VendorScoped": "VENDOR 사용자에게 자기 회사 설비의 행만 보여 줄 테이블",
    "ProcessParameter": "VENDOR 사용자에게 가릴 공정 센서 컬럼",
}


def _policy_tags(labels: list[dict] | None) -> list[str]:
    prefix = CLASSIFICATION + "."
    return [t["tagFQN"][len(prefix) :] for t in labels or [] if t["tagFQN"].startswith(prefix)]


def snapshot(tables: list[dict]) -> dict:
    """OpenMetadata 테이블 목록(fields=tags,columns)을 스냅샷 dict로 바꾼다."""
    found: dict[str, dict[str, set[str]]] = {}
    for table in tables:
        name = table["fullyQualifiedName"].removeprefix(DATABASE + ".")
        for tag in _policy_tags(table.get("tags")):
            found.setdefault(tag, {}).setdefault("tables", set()).add(name)
        for col in table.get("columns") or []:
            for tag in _policy_tags(col.get("tags")):
                found.setdefault(tag, {}).setdefault("columns", set()).add(f"{name}.{col['name']}")
    tags = {
        tag: {kind: sorted(names) for kind, names in sorted(kinds.items(), reverse=True)}
        for tag, kinds in sorted(found.items())
    }
    return {"classification": CLASSIFICATION, "synced_from": "openmetadata", "tags": tags}


def pull(token: str) -> None:
    query = f"database={DATABASE}&fields=tags,columns&limit=1000"
    tables = call(f"/tables?{query}", token)["data"]
    data = snapshot(tables)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    SNAPSHOT.write_text(HEADER + text, encoding="utf-8")
    counts = {tag: sum(map(len, kinds.values())) for tag, kinds in data["tags"].items()}
    print(f"{SNAPSHOT.relative_to(ROOT)} 갱신: {counts}")


def push(token: str) -> None:
    data = yaml.safe_load(SNAPSHOT.read_text(encoding="utf-8"))
    tags = data.get("tags") or {}
    body = {"name": CLASSIFICATION, "description": DESCRIPTIONS[CLASSIFICATION]}
    call("/classifications", token, body, method="PUT")
    for tag in tags:
        body = {
            "name": tag,
            "classification": CLASSIFICATION,
            "description": DESCRIPTIONS.get(tag, tag),
        }
        call("/tags", token, body, method="PUT")

    # 테이블마다 붙일 태그를 모아 PATCH 한 번
    wanted: dict[str, list[tuple[str | None, str]]] = {}
    for tag, kinds in tags.items():
        for name in kinds.get("tables") or []:
            wanted.setdefault(name, []).append((None, tag))
        for name in kinds.get("columns") or []:
            table, col = name.rsplit(".", 1)
            wanted.setdefault(table, []).append((col, tag))

    for table, items in wanted.items():
        try:
            t = call(f"/tables/name/{DATABASE}.{table}?fields=tags,columns", token)
        except urllib.error.HTTPError as e:
            sys.exit(f"{table}: OpenMetadata에서 찾지 못했다 ({e.code}). make om-ingest 먼저")
        cols = {c["name"]: i for i, c in enumerate(t["columns"])}
        ops = []
        for col, tag in items:
            label = {
                "tagFQN": f"{CLASSIFICATION}.{tag}",
                "source": "Classification",
                "labelType": "Manual",
                "state": "Confirmed",
            }
            target = t if col is None else t["columns"][cols[col]]
            if tag in _policy_tags(target.get("tags")):
                continue
            target["tags"] = (target.get("tags") or []) + [label]
            path = "/tags" if col is None else f"/columns/{cols[col]}/tags"
            ops = [op for op in ops if op["path"] != path]
            ops.append({"op": "add", "path": path, "value": target["tags"]})
        if ops:
            call(f"/tables/{t['id']}", token, ops, method="PATCH")
        print(f"{table}: {len(ops)}곳에 붙임" if ops else f"{table}: 이미 붙어 있음")


if __name__ == "__main__":
    if "--push" in sys.argv[1:]:
        push(admin_token())
    else:
        pull(admin_token())
