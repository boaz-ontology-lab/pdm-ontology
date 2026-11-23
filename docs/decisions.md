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
| 14 | 데모 설비는 C 문서를 따른다. 데모 2번은 설비 17의 2015-11-27 comp2 고장, 동시 고장 테스트는 설비 88의 2015-11-30 comp2, comp4 고장이다. 짝인 원본 교체 기록을 숨겨 미해결로 만든다 | C의 테스트 케이스와 예시가 이미 설비 17 기준이다. 두 고장 모두 숨긴 기록 뒤로 같은 부품 교체가 없어 새 resolvedBy 규칙에서도 미해결로 남는다 |
| 15 | 거부와 오류는 HTTP 코드로 구분한다. 권한 403, 사전조건 409, 입력 422, 대상 없음 404, 사용자 확인 401. 본문 모양은 모두 같다. 자세한 내용은 [api.md](api.md) | 발표에서 단계별 거부가 바로 보이고, 테스트와 로그에서 구분하기 쉽다. LLM 도구는 상태 코드와 상관없이 본문을 넘기면 된다 |
| 16 | `INVALID_COMPONENT`는 사전조건이 아니라 입력 오류(422)로 다룬다. 입력 스키마에서 `comp`를 enum으로 막는다 (18번으로 대체)| 설비 상태와 상관없이 처음부터 틀린 값이다 |
| 17 | 객체와 감사 로그 ID는 정수, 사용자 ID만 문자열(`fw1`, `mech1`)이다 | 적재 데이터의 ID가 정수라 조인과 타입이 단순해진다 |
| 18 | `comp`를 enum 대신 `ref: Component`로 막는다. | enum 값 `comp1`이 소문자라 표기 검사에 걸리고 부품 목록을 두 곳에 두게 된다. |
| 20 | 파생 속성은 `ontology/objects.yaml`에 사람이 읽는 `description`과 엔진이 쓰는 `sql`로 적는다. `sql`은 다른 객체를 테이블 이름 대신 `{FailureEvent} f`처럼 객체 타입으로 부른다 | 엔진이 단순하고 필터, 정렬, explain, 정책이 모두 DB 안에서 처리된다. 객체 타입으로 부르면 원본과 ops를 합친 뷰를 놓치지 않고, 엔진이 그 자리에 벤더 행 필터를 넣을 수 있다 |

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
| `MEDIUM` | 어느 부품이든 마지막 교체 후 90일이 넘었다 | 45대 |
| `LOW` | 그 외 | 50대 |

대수는 데모용으로 숨긴 교체 기록(결정 14)을 뺀 `core.maintenance` 기준이다. 숨기기 전에는 MEDIUM 44대, LOW 51대였다. 설비 17은 comp2 교체 기록이 숨겨져 마지막 comp2 교체가 110일 전이 되면서 MEDIUM이 된다.

고장 여부는 status가 보여 주므로 riskLevel에 넣지 않는다. 판정 근거는 `riskReasons`로 함께 돌려준다.

## 아직 정하지 않은 것

없음
