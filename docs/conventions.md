# 표기 규칙

팀원마다 표기가 달라 충돌이 나지 않도록, 이름과 값은 이 문서를 기준으로 쓴다.
규칙을 바꾸려면 이 문서를 고치는 PR을 먼저 올리고 세 명이 모두 승인한다.

`ontology/*.yaml`의 이름과 코드 값은 CI가 `scripts/check_conventions.py`로 검사한다.

## 1. 온톨로지 이름

| 대상 | 규칙 | 예 | 잘못된 예 |
|---|---|---|---|
| 객체 타입 | PascalCase, 단수 | `Machine`, `FailureEvent`, `WorkOrder` | `machine`, `Machines` |
| 속성 | camelCase | `lastRestartAt`, `errorCount7d` | `last_restart_at` |
| 식별자 | camelCase + `Id` | `machineId`, `failureId`, `workOrderId` | `machineID`, `machine_id` |
| 참조 키 | 가리키는 PK와 같은 이름 | `WorkOrder.inspectionRequestId` | `WorkOrder.requestId` |
| 부품 | `comp` | `comp: comp2` | `component`, `failure` |
| 시각 | `~At` | `createdAt`, `completedAt` | `createTime` |
| 사람 | `~By` 또는 `assigneeId` (값은 userId) | `createdBy`, `requestedBy` | `creator` |
| 링크 | camelCase, 동사나 관계로 시작 | `hasFailures`, `resolvedBy`, `addresses` | `FailureLink` |
| 액션 | camelCase, 동사로 시작 | `requestInspection`, `restartMachine` | `RequestInspection` |
| 파생 속성 | 속성과 같음. 참거짓은 `has~`, `is~` | `hasOpenFailure`, `isResolved` | `openFailureFlag` |

원본 CSV의 컬럼 이름은 적재할 때 이 규칙에 맞게 바꾼다.

| 원본 컬럼 | 온톨로지 속성 |
|---|---|
| `machineID` | `machineId` |
| `errorID` (error1~5) | `errorType` |
| `failure` (comp1~4) | `comp` |

`errorID`는 에러 종류를 뜻하는 코드라서, 식별자로 오해하지 않도록 `errorType`으로 바꾼다.

## 2. 코드 값 (enum)

- 영어 대문자와 밑줄(UPPER_SNAKE)로 쓴다. 예: `RUNNING`, `IN_PROGRESS`
- 다이어그램과 문서에서는 괄호 안에 한글 뜻을 붙인다. 예: `RUNNING (가동 중)`
- YAML, 코드, API, DB 어디에서도 소문자나 한글 값을 쓰지 않는다.

| 속성 | 값 |
|---|---|
| `Machine.status` (파생) | `DOWN` (고장 방치), `MAINTENANCE` (정비 중), `READY` (재가동 대기), `RUNNING` (가동 중) |
| `Machine.riskLevel` (파생) | `HIGH` (높음), `MEDIUM` (중간), `LOW` (낮음) |
| `WorkOrder.type` | `CORRECTIVE` (고장 수리), `PREVENTIVE` (예방 정비) |
| `WorkOrder.status` | `OPEN` (진행 중), `DONE` (완료), `CANCELED` (취소) |
| `InspectionRequest.status` | `REQUESTED` (요청), `CONVERTED` (작업지시로 전환), `CLOSED` (종료) |
| `Maintenance.source` | `RAW` (원본), `ACTION` (액션) |
| `Machine.riskReasons[].rule` (파생) | `RECENT_ERROR_NOT_REPLACED` (최근 에러 부품 미교체), `REPLACED_OVER_90_DAYS` (교체 후 90일 초과) |
| `Maintenance.reason` (파생) | `CORRECTIVE` (고장), `PREVENTIVE` (예방) |
| `User.role` | `FIELD_WORKER` (현장 작업자), `MECHANIC` (정비사), `MANAGER` (관리자), `VENDOR` (벤더) |

액션 거부 코드도 같은 규칙을 따른다. 예: `ROLE_NOT_ALLOWED`, `UNRESOLVED_FAILURE`

## 3. 용어

같은 개념은 문서와 대화에서 같은 말로 부른다.

| 한글 | 영어 이름 | 뜻 |
|---|---|---|
| 고장 | `FailureEvent` | 부품이 고장 나서 교체한 사건 |
| 교체 기록 (정비 기록) | `Maintenance` | 부품을 교체한 기록. 원본과 액션 기록을 합친 것 |
| 작업지시 | `WorkOrder` | 부품을 교체하라는 지시 |
| 점검 요청 | `InspectionRequest` | 설비를 봐 달라는 요청. 설비를 멈추지 않는다 |
| OPEN 작업지시 | | status가 `OPEN`인 작업지시. "열린 작업지시"라고 쓰지 않는다 |
| 미해결 고장 | | resolvedBy로 이어진 교체 기록이 없는 고장 |
| 기준 시각 (NOW) | | 가상 시계 시각. 2016-01-01 06:00 고정 |

## 4. DB

| 대상 | 규칙 | 예 |
|---|---|---|
| 스키마 | 역할별 4개 | `raw`, `ref`, `ops`, `core` |
| 테이블 | snake_case, 복수 | `ops.work_orders`, `raw.failures` |
| 컬럼 | snake_case | `machine_id`, `created_at` |
| 뷰 | `core` 스키마, 객체 이름의 snake_case | `core.maintenance`, `core.machine` |

| 스키마 | 담는 것 | 앱 권한 |
|---|---|---|
| `raw` | 원천 시스템에서 왔다고 보는 데이터 (원본 CSV, 합성 users, vendors, vendor_models, components) | 읽기만 |
| `ref` | 우리 팀이 판단해 덧붙인 데이터 (error_component_map, 데모용 숨김 목록) | 읽기만 |
| `ops` | 액션이 쓰는 데이터 (work_orders, inspection_requests, maintenance_log, machine_state, audit_log) | 읽기, 쓰기 |
| `core` | 여러 테이블을 합친 뷰 | 읽기만 |

DB의 snake_case와 온톨로지의 camelCase는 `ontology/objects.yaml`의 `column`으로 이어 준다.

## 5. API

- 요청과 응답의 JSON 키는 camelCase로 쓴다. 예: `machineId`, `reasonCode`
- 객체 타입은 경로에 그대로 쓴다. 예: `GET /objects/Machine/17`
- 액션 이름은 경로에 그대로 쓴다. 예: `POST /actions/restartMachine`
- 실행자는 `X-User-Id` 헤더로 보내고, 역할은 서버가 찾는다.
- 시각은 ISO 8601 문자열이고, 실제 시각이 아니라 가상 시계 시각이다. 예: `2016-01-01T06:00:00`

## 6. Python 코드

- PEP 8을 따르고, 형식은 ruff가 맞춘다. 함수와 변수는 snake_case, 클래스는 PascalCase.
- 온톨로지 이름(camelCase)은 문자열 값으로만 다루고, 파이썬 변수 이름에 억지로 맞추지 않는다.
- 현재 시각은 반드시 `clock.now()`로 얻는다. `datetime.now()`를 직접 부르지 않는다.
