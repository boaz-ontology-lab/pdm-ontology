# data

적재 스크립트와 합성 데이터 스크립트를 둔다. 담당은 A.

## 원본 데이터

Kaggle의 Microsoft Azure Predictive Maintenance 데이터셋을 내려받아 `data/raw/`에 둔다.
용량이 커서 git에는 올리지 않는다(`.gitignore`에 포함).

```
data/raw/
  PdM_telemetry.csv
  PdM_errors.csv
  PdM_failures.csv
  PdM_maint.csv
  PdM_machines.csv
```

## 적재

```bash
make up-db                    # Postgres만 실행 (make가 없으면 docker compose -f infra/docker-compose.yml up -d postgres)
uv run python data/load.py    # DB를 처음 상태로: db/*.sql 실행 → 원본 적재 → 합성 → 확인
```

DB 접속 값은 환경 변수, `infra/.env`, compose 기본값 순서로 정한다(`app/db.py`). 포트를 바꿨다면 `infra/.env`만 고치면 compose와 `load.py`가 같은 값을 쓴다.

`load.py`는 실행할 때마다 스키마를 지우고 다시 만든다. 테이블 정의를 고쳤거나 DB가 꼬였을 때 다시 실행하면 된다.
`ops`의 액션 기록도 지워진다. 확인 항목 중 하나라도 어긋나면 전체를 되돌린다.

## 규칙

- 원본 CSV는 고치지 않는다. 컬럼 이름만 적재할 때 snake_case로 바꾼다. `errorID`는 `error_type`, `failure`는 `comp`로 바꾼다.
- 합성 데이터는 seed 42로 만들고, 모든 시각은 기준 시각(2016-01-01 06:00)보다 앞서야 한다.
- 무엇을 어떤 규칙으로 만들었는지는 `docs/synthetic-data.md`에 적는다.
