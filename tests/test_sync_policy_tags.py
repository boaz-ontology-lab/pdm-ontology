"""sync_policy_tags.snapshot이 OpenMetadata 응답을 policy_tags.yaml 형식으로 바꾸는지 본다."""

import yaml
from sync_policy_tags import DATABASE, SNAPSHOT, snapshot


def label(tag: str) -> dict:
    return {"tagFQN": f"PdmPolicy.{tag}"}


def test_snapshot_matches_committed_file():
    committed = yaml.safe_load(SNAPSHOT.read_text(encoding="utf-8"))
    # 파일에 적힌 대로 태그가 붙은 OpenMetadata 응답을 만든다
    tables: dict[str, dict] = {}

    def table(name: str) -> dict:
        fqn = f"{DATABASE}.{name}"
        return tables.setdefault(name, {"fullyQualifiedName": fqn, "tags": [], "columns": []})

    for tag, kinds in committed["tags"].items():
        for name in kinds.get("tables", []):
            table(name)["tags"].append(label(tag))
        for name in kinds.get("columns", []):
            t, col = name.rsplit(".", 1)
            table(t)["columns"].append({"name": col, "tags": [label(tag)]})
    # 다른 분류의 태그와 태그 없는 테이블은 무시한다
    table("raw.machines")["tags"].append({"tagFQN": "PII.Sensitive"})

    got = snapshot(list(tables.values()))
    assert got["synced_from"] == "openmetadata"
    assert got["tags"] == committed["tags"]
