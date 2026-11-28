from __future__ import annotations

import psycopg
import pytest

from app import db


@pytest.fixture(scope="session")
def pg():
    """data/load.py로 적재한 로컬 Postgres. 붙을 수 없거나 적재 전이면 DB 테스트를 건너뛴다.

    CI에는 DB가 없어서 건너뛴다. 로컬에서는 `make up-db && uv run python data/load.py` 뒤에 돈다.
    """
    try:
        conn = db.connect(connect_timeout=2)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres에 붙을 수 없습니다: {e}".splitlines()[0])
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('core.machine') IS NOT NULL")
            if not cur.fetchone()[0]:
                pytest.skip("core.machine이 없습니다. data/load.py로 적재하세요")
        yield conn
