"""DB를 처음 상태로 만든다: 테이블 생성 → 원본 적재 → 합성 → 확인.

    uv run python data/load.py

매번 스키마를 지우고 다시 만든다. ops의 액션 기록도 지워진다.
하나라도 실패하면 전체를 되돌린다 (트랜잭션 하나).
규칙과 값의 근거는 docs/synthetic-data.md
"""

import os
from pathlib import Path

import psycopg
from faker import Faker

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SQL_DIR = ROOT / "db"
SEED = 42

# CSV 파일, 테이블, 컬럼. 컬럼 순서는 CSV 헤더 순서와 같다
RAW_FILES = [
    ("PdM_machines.csv", "raw.machines", "machine_id, model, age"),
    (
        "PdM_telemetry.csv",
        "raw.telemetry",
        "datetime, machine_id, volt, rotate, pressure, vibration",
    ),
    ("PdM_errors.csv", "raw.errors", "datetime, machine_id, error_type"),
    ("PdM_failures.csv", "raw.failures", "datetime, machine_id, comp"),
    ("PdM_maint.csv", "raw.maintenance", "datetime, machine_id, comp"),
]

VENDORS = [(1, "Vendor A"), (2, "Vendor B")]
VENDOR_MODELS = [("model1", 1), ("model2", 1), ("model3", 2), ("model4", 2)]
# (user_id, role, vendor_id). 이름은 Faker로 채운다
USERS = [
    ("fw1", "FIELD_WORKER", None),
    ("fw2", "FIELD_WORKER", None),
    ("mech1", "MECHANIC", None),
    ("mech2", "MECHANIC", None),
    ("mech3", "MECHANIC", None),
    ("mgr1", "MANAGER", None),
    ("venA", "VENDOR", 1),
    ("venB", "VENDOR", 2),
]
COMPONENTS = [(f"comp{i}", f"comp{i}") for i in range(1, 5)]
ERROR_COMPONENT_MAP = [
    ("error1", "comp1"),
    ("error2", "comp2"),
    ("error3", "comp2"),
    ("error4", "comp3"),
    ("error5", "comp4"),
]
# 데모용 미해결 고장과 짝인 교체 기록 (machine_id, datetime, comp). 결정 14
HIDDEN = [
    (17, "2015-11-27 06:00", "comp2"),
    (88, "2015-11-30 06:00", "comp2"),
    (88, "2015-11-30 06:00", "comp4"),
]


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "pdm"),
        user=os.getenv("POSTGRES_USER", "pdm"),
        password=os.getenv("POSTGRES_PASSWORD", "pdm"),
    )


def create_tables(cur: psycopg.Cursor) -> None:
    for path in sorted(SQL_DIR.glob("*.sql")):
        print(f"  {path.name}")
        cur.execute(path.read_text(encoding="utf-8"))


def load_raw(cur: psycopg.Cursor) -> None:
    # COPY는 파일 순서대로 넣으므로 IDENTITY 번호가 CSV 행 순서와 같다
    for file, table, columns in RAW_FILES:
        sql = f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        with open(RAW_DIR / file, "rb") as f, cur.copy(sql) as copy:
            while chunk := f.read(1 << 20):
                copy.write(chunk)
        print(f"  {table}: {cur.rowcount}행")


def load_synthetic(cur: psycopg.Cursor) -> None:
    fake = Faker("ko_KR")
    fake.seed_instance(SEED)
    users = [(uid, fake.name(), role, vid) for uid, role, vid in USERS]

    cur.executemany("INSERT INTO raw.vendors VALUES (%s, %s)", VENDORS)
    cur.executemany("INSERT INTO raw.vendor_models VALUES (%s, %s)", VENDOR_MODELS)
    cur.executemany("INSERT INTO raw.users VALUES (%s, %s, %s, %s)", users)
    cur.executemany("INSERT INTO raw.components VALUES (%s, %s)", COMPONENTS)
    cur.executemany("INSERT INTO ref.error_component_map VALUES (%s, %s)", ERROR_COMPONENT_MAP)
    cur.executemany(
        """INSERT INTO ref.hidden_maintenance
           SELECT maint_id FROM raw.maintenance
           WHERE machine_id = %s AND datetime = %s AND comp = %s""",
        HIDDEN,
    )
    for uid, name, role, _ in users:
        print(f"  {uid:6} {role:13} {name}")


def check(cur: psycopg.Cursor) -> None:
    """적재 결과가 docs/synthetic-data.md와 같은지 확인한다. 다르면 전체를 되돌린다."""

    def one(sql: str):
        cur.execute(sql)
        return cur.fetchone()[0]

    expected_counts = {
        "raw.machines": 100,
        "raw.telemetry": 876100,
        "raw.errors": 3919,
        "raw.failures": 761,
        "raw.maintenance": 3286,
        "raw.users": 8,
    }
    for table, n in expected_counts.items():
        assert one(f"SELECT count(*) FROM {table}") == n, f"{table} 행 수가 {n}이 아니다"

    cur.execute("SELECT machine_id, comp, datetime::text FROM raw.failures WHERE failure_id = 121")
    demo = cur.fetchone()
    assert demo == (17, "comp2", "2015-11-27 06:00:00"), "failure_id 121은 데모 고장이어야 한다"

    vendor_a = """SELECT count(*) FROM raw.machines JOIN raw.vendor_models USING (model)
                  WHERE vendor_id = 1"""
    assert one(vendor_a) == 33, "벤더 A 설비는 33대여야 한다"

    hidden = "SELECT array_agg(maint_id ORDER BY maint_id) FROM ref.hidden_maintenance"
    assert one(hidden) == [564, 2898, 2899], "숨김 교체 기록은 564, 2898, 2899여야 한다"

    # 감사 로그는 추가만 된다 (테스트 A3). 시험 행은 savepoint와 함께 되돌린다
    try:
        with cur.connection.transaction():
            cur.execute("""INSERT INTO ops.audit_log (action, result, input, at)
                           VALUES ('check', 'ALLOWED', '{}', now())""")
            cur.execute("UPDATE ops.audit_log SET action = 'changed'")
    except psycopg.errors.RaiseException:
        pass
    else:
        raise AssertionError("ops.audit_log UPDATE가 막혀야 한다")

    # 뷰와 액션 테이블. 숨김 3건을 뺀 원본 교체 기록만 보이고, ops는 비어 있다
    view_and_ops_counts = {
        "core.maintenance": 3286 - 3,
        "core.machine": 100,
        "ops.inspection_requests": 0,
        "ops.work_orders": 0,
        "ops.maintenance_log": 0,
        "ops.machine_state": 0,
        "ops.audit_log": 0,
    }
    for table, n in view_and_ops_counts.items():
        assert one(f"SELECT count(*) FROM {table}") == n, f"{table} 행 수가 {n}이 아니다"


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        print("테이블 생성")
        create_tables(cur)
        print("원본 적재")
        load_raw(cur)
        print("합성")
        load_synthetic(cur)
        check(cur)
    print("완료: 확인 항목 모두 통과")


if __name__ == "__main__":
    main()
