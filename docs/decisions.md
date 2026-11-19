# 결정 기록

팀에서 정한 것을 번호를 붙여 쌓는다. 이미 정한 것을 바꿀 때는 기존 항목을 지우지 말고,
새 번호로 추가한 뒤 기존 항목에 "N번으로 대체"라고 적는다.

## D1

| 번호 | 결정 | 이유 |
|---|---|---|
| 1 | Telemetry는 객체로 두지 않고 Machine의 시계열 속성으로 둔다. "최근 24시간 평균 진동" 같은 파생 속성만 뽑는다 | 약 87만 행을 행마다 객체로 만들면 무겁다 |
| 2 | Component는 종류 4개(comp1~4)로 둔다 | 설비별 부품(400개)까지 나눌 질문이 MVP에 없다 |
| 3 | 에러, 고장, 정비에는 적재할 때 일련번호를 붙인다 (`errorEventId`, `failureId`, `maintId`) | 원본 CSV에 PK가 없다 |
| 4 | 고장 761건 중 743건은 같은 시각의 교체 기록과 짝이 맞는다. 고장 기록은 고장 때문에 부품을 교체한 기록이라 정비 기록과 같은 사건이다. 나머지 18건은 모두 2015-01-02 03:00에 몰린 데이터 경계 문제로 보이며, 모두 나중에 같은 부품의 교체 기록이 있다. 합성 교체 기록은 만들지 않는다 | 아래 resolvedBy 규칙이면 18건도 자연스럽게 해결된다 |
| 5 | 벤더는 모델 단위로 매핑한다. 벤더 A: model1, model2 (33대), 벤더 B: model3, model4 (67대) | 행 필터 데모가 눈에 띈다 |
| 6 | 교체 완료 액션은 교체 기록을 `ops.maintenance_log`에 쓰고, 조회할 때 `core.maintenance` 뷰로 원본과 합친다. 원본에는 다시 쓰지 않는다. 액션 기록에는 `workOrderId`만 두고, 어느 고장을 고친 것인지는 completedAs와 addresses 링크로 찾는다 | 원본은 읽기 전용이고, ops만 비우면 데모를 초기화할 수 있다 |
| 7 | 기준 시각은 2016-01-01 06:00으로 고정한다 | 데이터가 끝나는 시각이라 미래 데이터가 섞이지 않는다 |
| 8 | 코드 값은 영어 대문자로 쓰고, 다이어그램에서는 괄호 안에 한글 뜻을 적는다 | 팀원마다 한글 표기가 달라 충돌이 났다 |
| 9 | riskLevel은 `HIGH` / `MEDIUM` / `LOW` 3단계다 (아래 규칙) | 단일 조건은 100대 중 50대를 위험으로 잡았다 |
| 10 | 역할과 작업지시 값은 C 문서를 따른다. 점검 요청은 WorkOrder가 아니라 InspectionRequest로 따로 둔다 | 점검 요청은 설비를 멈추지 않으니 MAINTENANCE 계산에서 빠져야 한다 |
| 11 | 고장 해결 여부는 저장하지 않는다. `isResolved`는 resolvedBy 링크로 계산하고, 교체 완료 액션은 교체 기록만 만든다 | 같은 정보를 두 곳에 두면 어긋난다. 원본 failures 테이블에 쓰지 않아도 된다 |
| 12 | `Machine.status`는 저장하지 않고 파생 속성으로 조회할 때 계산한다. 저장하는 값은 `lastRestartAt`, `lastRestartBy`뿐이다 | 데모 초기화나 재적재 때 저장 값이 어긋나지 않는다 |
| 13 | 키 이름을 통일한다. 자세한 규칙은 [conventions.md](conventions.md) | 같은 키를 machineID, machineId, machine_id로 섞어 썼다 |

## 핵심 규칙

### resolvedBy (FailureEvent → Maintenance, N:0..1)

같은 `machineId`, 같은 `comp`이고, 교체 `datetime`이 고장 `datetime`보다 같거나 늦은 교체 기록 중 가장 이른 것.

```sql
SELECT DISTINCT ON (f.failure_id) f.failure_id, m.maint_id
FROM raw.failures f
JOIN core.maintenance m
  ON  m.machine_id = f.machine_id
  AND m.comp       = f.comp
  AND m.datetime  >= f.datetime
ORDER BY f.failure_id, m.datetime;
```

### Machine.status (위에서부터 먼저 맞는 것)

| 순위 | 값 | 조건 |
|---|---|---|
| 1 | `DOWN` | `hasUncoveredFailure`: OPEN 작업지시가 걸리지 않은 미해결 고장이 있다 |
| 2 | `MAINTENANCE` | `hasOpenWorkOrder`: OPEN 작업지시가 있다 |
| 3 | `READY` | 마지막 재가동(`lastRestartAt`) 이후 DONE이 된 작업지시가 있다 |
| 4 | `RUNNING` | 그 외 |

재가동 액션은 `READY`일 때만 허용한다.

### Machine.riskLevel (위에서부터 먼저 맞는 것)

| 값 | 조건 | 기준 시각 대수 |
|---|---|---|
| `HIGH` | 최근 24시간 안에 에러가 있고, 그 에러가 가리키는(indicates) 부품을 에러 이후 아직 교체하지 않았다 | 5대 |
| `MEDIUM` | 어느 부품이든 마지막 교체 후 90일이 넘었다 | 44대 |
| `LOW` | 그 외 | 51대 |

고장 여부는 status가 보여 주므로 riskLevel에 넣지 않는다. 판정 근거는 `riskReasons`로 함께 돌려준다.

## 아직 정하지 않은 것

| 항목 | 후보 | 메모 |
|---|---|---|
| 데모 설비 | 설비 17 (2015-11-27 comp2) / 설비 20 (2015-12-30 comp4) | 둘 다 교체 기록을 숨기면 미해결로 남는다. 동시 고장 테스트는 설비 88 |
| 거부 응답의 HTTP 코드 | 403, 409로 구분 / 모두 200 + `result` | 조회 API와 함께 정한다 |
