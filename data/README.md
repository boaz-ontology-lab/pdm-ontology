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

## 규칙

- 원본 CSV는 고치지 않는다. 컬럼 이름만 적재할 때 snake_case로 바꾼다.
- 합성 데이터는 seed 42로 만들고, 모든 시각은 기준 시각(2016-01-01 06:00)보다 앞서야 한다.
- 무엇을 어떤 규칙으로 만들었는지는 `docs/synthetic-data.md`에 적는다.
