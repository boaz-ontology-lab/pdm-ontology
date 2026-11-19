"""ontology/*.yaml의 이름과 코드 값이 docs/conventions.md를 따르는지 검사한다.

사용법:
    python scripts/check_conventions.py            # ontology/ 폴더 검사
    python scripts/check_conventions.py path/to/dir

검사하는 것:
    - 객체 타입 이름: PascalCase
    - 속성, 파생 속성, 링크, 액션, 액션 입력 이름: camelCase, 끝이 "ID"가 아님
    - enum 값, allowed_roles, 거부 코드: UPPER_SNAKE

YAML의 예약 키(source, properties, allowed_roles 같은 것)는 snake_case로 쓰고 검사하지 않는다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# actions.yaml에서 액션이 아닌 공통 정의 블록
ACTION_RESERVED = {"reason_codes"}


def _check_name(name: str, pattern: re.Pattern[str], kind: str, where: str) -> list[str]:
    errors = []
    if not pattern.match(name):
        errors.append(f"{where}: {kind} '{name}' 형식이 맞지 않습니다")
    elif name.endswith("ID"):
        errors.append(f"{where}: {kind} '{name}'는 'ID' 대신 'Id'로 끝나야 합니다")
    return errors


def _check_enum(spec: Any, where: str) -> list[str]:
    if not isinstance(spec, dict) or spec.get("type") != "enum":
        return []
    errors = []
    for value in spec.get("values") or []:
        if not isinstance(value, str) or not UPPER_SNAKE.match(value):
            errors.append(f"{where}: enum 값 '{value}'는 UPPER_SNAKE여야 합니다")
    return errors


def check_objects(data: dict[str, Any], file: str = "objects.yaml") -> list[str]:
    errors: list[str] = []
    for type_name, spec in (data or {}).items():
        errors += _check_name(type_name, PASCAL, "객체 타입", file)
        if not isinstance(spec, dict):
            continue
        for section in ("properties", "derived"):
            for prop, prop_spec in (spec.get(section) or {}).items():
                where = f"{file} {type_name}.{section}"
                errors += _check_name(prop, CAMEL, "속성", where)
                errors += _check_enum(prop_spec, f"{where}.{prop}")
    return errors


def check_links(data: dict[str, Any], file: str = "links.yaml") -> list[str]:
    errors: list[str] = []
    for link_name, spec in (data or {}).items():
        errors += _check_name(link_name, CAMEL, "링크", file)
        if not isinstance(spec, dict):
            continue
        for end in ("from", "to"):
            target = spec.get(end)
            if isinstance(target, str):
                errors += _check_name(target, PASCAL, f"{end} 객체 타입", f"{file} {link_name}")
    return errors


def check_actions(data: dict[str, Any], file: str = "actions.yaml") -> list[str]:
    errors: list[str] = []
    for code in (data or {}).get("reason_codes") or {}:
        if not UPPER_SNAKE.match(code):
            errors.append(f"{file} reason_codes: 거부 코드 '{code}'는 UPPER_SNAKE여야 합니다")

    for action_name, spec in (data or {}).items():
        if action_name in ACTION_RESERVED:
            continue
        errors += _check_name(action_name, CAMEL, "액션", file)
        if not isinstance(spec, dict):
            continue
        where = f"{file} {action_name}"
        target = spec.get("target")
        if isinstance(target, str):
            errors += _check_name(target, PASCAL, "target 객체 타입", where)
        for role in spec.get("allowed_roles") or []:
            if not isinstance(role, str) or not UPPER_SNAKE.match(role):
                errors.append(f"{where}: 역할 '{role}'는 UPPER_SNAKE여야 합니다")
        for field, field_spec in (spec.get("input") or {}).items():
            errors += _check_name(field, CAMEL, "입력", f"{where}.input")
            errors += _check_enum(field_spec, f"{where}.input.{field}")
    return errors


CHECKERS = {
    "objects.yaml": check_objects,
    "links.yaml": check_links,
    "actions.yaml": check_actions,
}


def check_dir(directory: Path) -> tuple[list[str], list[str]]:
    """(검사한 파일, 오류 목록)을 돌려준다."""
    checked: list[str] = []
    errors: list[str] = []
    for filename, checker in CHECKERS.items():
        path = directory / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            errors.append(f"{filename}: 최상위는 객체(매핑)여야 합니다")
            continue
        checked.append(filename)
        errors += checker(data, filename)
    return checked, errors


def main(argv: list[str]) -> int:
    directory = Path(argv[1]) if len(argv) > 1 else Path("ontology")
    checked, errors = check_dir(directory)
    if not checked:
        print(f"{directory}에 검사할 YAML이 없어 건너뜁니다")
        return 0
    if errors:
        print("\n".join(errors))
        print(f"\n표기 규칙 위반 {len(errors)}건. docs/conventions.md를 확인하세요.")
        return 1
    print(f"표기 규칙 통과: {', '.join(checked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
