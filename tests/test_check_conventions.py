from pathlib import Path

from check_conventions import check_actions, check_dir, check_links, check_objects


def test_objects_pass():
    data = {
        "Machine": {
            "properties": {"machineId": {"type": "int"}, "lastRestartAt": {"type": "datetime"}},
            "derived": {
                "status": {"type": "enum", "values": ["DOWN", "MAINTENANCE", "READY", "RUNNING"]},
            },
        }
    }
    assert check_objects(data) == []


def test_objects_catch_bad_names_and_values():
    data = {
        "machine": {
            "properties": {"machineID": {"type": "int"}, "last_restart_at": {"type": "datetime"}},
            "derived": {"status": {"type": "enum", "values": ["running", "가동"]}},
        }
    }
    errors = check_objects(data)
    assert any("'machine'" in e for e in errors)
    assert any("'machineID'" in e and "Id" in e for e in errors)
    assert any("'last_restart_at'" in e for e in errors)
    assert any("'running'" in e for e in errors)
    assert any("'가동'" in e for e in errors)


def test_links():
    assert check_links({"resolvedBy": {"from": "FailureEvent", "to": "Maintenance"}}) == []
    errors = check_links({"ResolvedBy": {"from": "failure", "to": "Maintenance"}})
    assert len(errors) == 2


def test_actions():
    data = {
        "reason_codes": {"ROLE_NOT_ALLOWED": {}, "notAssignee": {}},
        "restartMachine": {"target": "Machine", "allowed_roles": ["MANAGER"]},
        "CreateWorkOrder": {
            "target": "Machine",
            "allowed_roles": ["manager"],
            "input": {
                "component": {"type": "string"},
                "assigneeID": {"type": "string"},
                "type": {"type": "enum", "values": ["corrective", "PREVENTIVE"]},
            },
        },
    }
    errors = check_actions(data)
    assert any("'notAssignee'" in e for e in errors)
    assert any("'CreateWorkOrder'" in e for e in errors)
    assert any("'manager'" in e for e in errors)
    assert any("'assigneeID'" in e for e in errors)
    assert any("'corrective'" in e for e in errors)
    assert not any("restartMachine" in e for e in errors)


def test_check_dir_skips_missing_files(tmp_path: Path):
    checked, errors = check_dir(tmp_path)
    assert checked == []
    assert errors == []


def test_check_dir_reads_yaml(tmp_path: Path):
    (tmp_path / "links.yaml").write_text(
        "hasFailures:\n  from: Machine\n  to: FailureEvent\n", encoding="utf-8"
    )
    checked, errors = check_dir(tmp_path)
    assert checked == ["links.yaml"]
    assert errors == []
