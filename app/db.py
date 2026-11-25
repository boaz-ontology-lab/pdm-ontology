"""프로젝트 Postgres 연결 설정.

값은 아래 순서로 정한다. 앞에 있는 것이 이긴다.

1. 환경 변수. 컨테이너에서는 compose의 environment, env_file이 넣어 준다
2. infra/.env 파일. 로컬 개발용이고 없어도 된다 (infra/.env.example을 복사해 쓴다)
3. 기본값. infra/docker-compose.yml의 기본값과 같다

값이 빈 문자열이면 없는 것으로 본다. compose의 ${POSTGRES_DB:-pdm}과 같은 규칙이다.

컨테이너로 돌릴 때: POSTGRES_PORT는 compose에서 호스트에 여는 포트다. 같은 네트워크의 컨테이너는
postgres:5432로 붙어야 하므로, 앱 서비스의 environment에 POSTGRES_HOST=postgres,
POSTGRES_PORT=5432를 적는다. environment는 env_file보다 우선한다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import psycopg

ENV_FILE = Path(__file__).resolve().parents[1] / "infra" / ".env"

DEFAULTS = {
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "pdm",
    "POSTGRES_USER": "pdm",
    "POSTGRES_PASSWORD": "pdm",
}


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 줄만 읽는다. 빈 줄, # 주석, 앞의 export, 값을 감싼 따옴표는 무시한다."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def settings(environ: Mapping[str, str] | None = None, env_file: Path = ENV_FILE) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    file_values = read_env_file(env_file)
    return {
        key: environ.get(key) or file_values.get(key) or default
        for key, default in DEFAULTS.items()
    }


def connect(**kwargs) -> psycopg.Connection:
    s = settings()
    return psycopg.connect(
        host=s["POSTGRES_HOST"],
        port=s["POSTGRES_PORT"],
        dbname=s["POSTGRES_DB"],
        user=s["POSTGRES_USER"],
        password=s["POSTGRES_PASSWORD"],
        **kwargs,
    )
