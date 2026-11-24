"""OpenMetadata에 pdm DB(raw, ref, ops, core)를 수집한다 (#23).

    make up                           # Postgres + OpenMetadata
    uv run python data/load.py        # 수집할 테이블이 있어야 한다
    uv run python scripts/om_ingest.py

ingestion-bot 토큰을 OpenMetadata API로 받아 환경 변수로 넘기고,
infra/openmetadata/postgres.yaml을 ingestion 컨테이너에 복사해 `metadata ingest`로 실행한다.
여러 번 실행해도 된다. 바뀐 테이블과 컬럼만 갱신된다.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "infra" / "openmetadata" / "postgres.yaml"
CONTAINER = "openmetadata_ingestion"
OM_API = os.getenv("OM_API", "http://localhost:8585/api/v1")


def call(path: str, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(OM_API + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def admin_token() -> str:
    # 로컬 기본 관리자 계정 (README의 make up 설명과 같다)
    email = os.getenv("OM_ADMIN_EMAIL", "admin@open-metadata.org")
    password = os.getenv("OM_ADMIN_PASSWORD", "admin")
    body = {"email": email, "password": base64.b64encode(password.encode()).decode()}
    return call("/users/login", body=body)["accessToken"]


def bot_token(admin: str) -> str:
    bot = call("/bots/name/ingestion-bot", admin)
    return call(f"/users/auth-mechanism/{bot['botUser']['id']}", admin)["config"]["JWTToken"]


def main() -> None:
    env = {
        "OM_JWT_TOKEN": bot_token(admin_token()),
        "POSTGRES_USER": os.getenv("POSTGRES_USER", "pdm"),
        "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD", "pdm"),
        "POSTGRES_DB": os.getenv("POSTGRES_DB", "pdm"),
    }
    subprocess.run(["docker", "cp", str(CONFIG), f"{CONTAINER}:/tmp/pdm_ingest.yaml"], check=True)
    cmd = ["docker", "exec"]
    for name in env:
        cmd += ["-e", name]  # 값은 프로세스 환경으로만 넘겨 명령줄에 남기지 않는다
    cmd += [CONTAINER, "metadata", "ingest", "-c", "/tmp/pdm_ingest.yaml"]
    sys.exit(subprocess.run(cmd, env={**os.environ, **env}).returncode)


if __name__ == "__main__":
    main()
