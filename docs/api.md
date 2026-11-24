# API 명세

사람, 프론트, LLM 도구가 모두 이 API 하나로 온톨로지를 조회하고 액션을 실행한다.

- 공통 규칙은 B와 C가 함께, 조회 API는 B, 액션 API는 C가 관리한다.
- **액션의 입력 필드, 허용 역할, 사전조건, 거부 코드의 원본은 [`ontology/actions.yaml`](../ontology/README.md)이다.** 이 문서는 응답 모양과 예시만 다루고, 조건을 다시 나열하지 않는다.
- 코드가 생기면 FastAPI가 만드는 `/docs`(OpenAPI)가 실제 명세가 된다. 이 문서와 어긋나면 둘 중 맞는 쪽으로 고치는 PR을 올린다.
- 이름과 코드 값은 [표기 규칙](conventions.md)을 따른다.

## 1. 공통 규칙

### 요청

| 항목 | 규칙 |
|---|---|
| 기본 주소 | 로컬 `http://localhost:8000` |
| 실행자 | `X-User-Id` 헤더로 보낸다. 역할은 서버가 users에서 찾는다. 역할을 헤더로 받지 않는다 |
| 키 | JSON 키는 camelCase. 예: `machineId`, `reasonCode` |
| 코드 값 | UPPER_SNAKE. 예: `RUNNING`, `CORRECTIVE` |
| 시각 | ISO 8601, 시간대 없음. 실제 시각이 아니라 **가상 시계 시각**이다. 예: `2016-01-01T06:00:00` |
| ID | 객체와 감사 로그 ID는 정수. 사용자 ID만 문자열(`fw1`, `mech1`, `mgr1`, `venA`) |

### 응답 결과와 HTTP 코드

| 결과 | HTTP | `result` | `stage` | 예 |
|---|---|---|---|---|
| 성공 | 200 (액션으로 새로 만든 게 있으면 201) | `ALLOWED` | 없음 | 조회, 재가동 성공 |
| 사용자 확인 실패 | 401 | `ERROR` | `AUTH` | `X-User-Id`가 없거나 없는 사용자 |
| 권한 거부 | 403 | `DENIED` | `PERMISSION` | `ROLE_NOT_ALLOWED`, `NOT_ASSIGNEE` |
| 사전조건 거부 | 409 | `DENIED` | `PRECONDITION` | `UNRESOLVED_FAILURE`, `OPEN_WORK_ORDER` |
| 입력 오류 | 422 | `ERROR` | `INPUT` | 필수값 누락, 잘못된 필터, `comp5` |
| 대상 없음 | 404 | `ERROR` | `INPUT` | 없는 설비, 다른 벤더의 설비 |

- 거부도 정상적인 결과다. 액션은 거부되어도 감사 로그를 남기고 `actionId`를 돌려준다.
- 다른 벤더의 설비를 조회하면 403이 아니라 **404**를 돌려준다. 403은 그 설비가 있다는 사실을 알려 주기 때문이다.
- FastAPI의 기본 422 응답(`{"detail": [...]}`)은 예외 핸들러로 아래 실패 응답 모양으로 바꾼다.
- LLM 도구를 감싸는 코드는 상태 코드와 상관없이 응답 본문을 그대로 넘긴다.

### 실패 응답 모양

401, 403, 404, 409, 422 모두 같은 모양이다.

```json
{
  "result": "DENIED",
  "stage": "PRECONDITION",
  "reasonCode": "UNRESOLVED_FAILURE",
  "message": "미해결 고장 1건이 남아 있어 재가동할 수 없습니다",
  "violations": [
    {"code": "UNRESOLVED_FAILURE", "detail": {}}
  ],
  "actionId": 123
}
```

| 키 | 뜻 |
|---|---|
| `reasonCode` | 대표 사유. `violations`의 첫 번째 코드와 같다 |
| `violations` | 실패한 조건 전부. 권한 단계에서 막히면 사전조건은 검사하지 않는다 |
| `actionId` | 액션 요청일 때만 있다. 감사 로그 ID |

### 공통 코드

actions.yaml 밖에서 쓰는 코드다.

| 코드 | HTTP | 뜻 |
|---|---|---|
| `UNKNOWN_USER` | 401 | `X-User-Id`가 없거나 없는 사용자 |
| `NOT_FOUND` | 404 | 없는 객체 타입, 객체, 링크, 액션. 볼 수 없는 객체 |
| `INVALID_INPUT` | 422 | 필수값 누락, 형식 오류, enum에 없는 값 (`comp5` 포함) |
| `INVALID_FILTER` | 422 | 없는 속성으로 거르거나 연산자가 틀림 |

## 2. 조회 API

### 객체 표현

모든 조회 응답에서 객체 하나는 이 모양이다. 이 문서의 값은 모두 예시다.

```json
{
  "type": "Machine",
  "id": 17,
  "properties": {"machineId": 17, "model": "model1", "age": 18, "lastRestartAt": null},
  "derived": {
    "status": "DOWN",
    "hasOpenFailure": true,
    "riskLevel": "MEDIUM",
    "riskReasons": [{"rule": "REPLACED_OVER_90_DAYS", "comp": "comp3", "days": 121}]
  },
  "masked": []
}
```

- `properties`는 저장된 값, `derived`는 조회할 때 계산한 값이다.
- `masked`는 정책 때문에 가린 속성 이름 목록이다. 가린 속성의 값은 `null`이다.

### 엔드포인트

| 메서드와 경로 | 하는 일 |
|---|---|
| `GET /object-types` | 객체 타입, 속성, 파생 속성, 링크 목록 |
| `GET /objects/{type}` | 객체 목록. 필터, 정렬, 페이지 |
| `GET /objects/{type}/{id}` | 객체 하나 |
| `GET /objects/{type}/{id}/links/{link}` | 링크를 따라간 객체 목록 |
| `GET /objects/Machine/{id}/sensorReadings` | 센서 시계열 (`from`, `to`) |

### 목록 조회와 필터

```
GET /objects/Machine?riskLevel=HIGH&errorCount7d__gte=3&limit=20&offset=0
```

| 문법 | 뜻 | 예 |
|---|---|---|
| `속성=값` | 같음 | `status=DOWN` |
| `속성__gte=값`, `속성__lte=값` | 크거나 같음, 작거나 같음 | `errorCount7d__gte=3` |
| `속성__in=값1,값2` | 목록 중 하나 | `model__in=model1,model2` |
| `sort=속성`, `sort=-속성` | 오름차순, 내림차순 | `sort=-errorCount7d` |
| `limit`, `offset` | 페이지. `limit` 기본 50, 최대 500 | |

파생 속성으로도 거를 수 있다.

```json
{"type": "Machine", "total": 5, "items": [{"type": "Machine", "id": 23, "...": "..."}]}
```

### 링크 따라가기

```
GET /objects/Machine/17/links/hasFailures?isResolved=false
```

```json
{
  "from": {"type": "Machine", "id": 17},
  "link": "hasFailures",
  "to": "FailureEvent",
  "total": 1,
  "items": [
    {"type": "FailureEvent", "id": 742, "properties": {"datetime": "2015-11-27T06:00:00", "machineId": 17, "comp": "comp2"}, "derived": {"isResolved": false}, "masked": []}
  ]
}
```

### explain

어떤 목록 조회든 `?explain=true`를 붙이면, 엔진이 만든 SQL과 적용된 정책을 함께 돌려준다. 발표와 디버깅용이다.

```json
{
  "type": "Machine",
  "total": 33,
  "items": [],
  "explain": {
    "sql": "SELECT ... FROM core.machine WHERE model IN ('model1', 'model2') ...",
    "policy": {"rowFilter": "model IN vendor A models", "maskedColumns": ["volt", "rotate", "pressure", "vibration"]}
  }
}
```

### 벤더 조회 제한

| 대상 | VENDOR가 보는 것 |
|---|---|
| 설비, 에러, 고장, 교체 기록 | 자기 회사 모델의 설비와 그 기록만 |
| 센서 시계열 | 시각은 보이고 `volt`, `rotate`, `pressure`, `vibration`은 `null`, `masked`에 이름이 들어간다 |
| 다른 회사 설비를 ID로 직접 조회 | 404 `NOT_FOUND` |

정책 조건은 엔진이 SQL에 직접 넣으므로, 링크를 따라가도 제한을 우회할 수 없다.

## 3. 액션 API

```
POST /actions/{actionName}
X-User-Id: mgr1
Content-Type: application/json

{ ...actions.yaml의 input 필드... }
```

검사 순서는 사용자 확인 → 입력 검증 → 권한 → 사전조건 → 실행 → 감사 로그다.

### 성공 응답 모양

```json
{
  "actionId": 124,
  "action": "restartMachine",
  "result": "ALLOWED",
  "at": "2016-01-01T06:00:00",
  "actor": {"userId": "mgr1", "role": "MANAGER"},
  "target": {"type": "Machine", "id": 17},
  "created": [],
  "changes": [
    {"type": "Machine", "id": 17, "field": "status", "before": "READY", "after": "RUNNING"}
  ],
  "remaining": {}
}
```

| 키 | 뜻 |
|---|---|
| `created` | 새로 만든 객체 |
| `changes` | 바뀐 값마다 before와 after. 파생 속성(`status`)은 액션 전후에 조회한 값이다 |
| `remaining` | 이 설비에 아직 남은 일. 관리자가 다음에 할 일을 응답만 보고 알 수 있게 한다 |

### 액션 목록

조건과 거부 코드는 actions.yaml의 같은 이름 항목을 본다.

| 액션 | 실행 역할 | 대상 | 요청 본문 |
|---|---|---|---|
| `requestInspection` | FIELD_WORKER, MANAGER | Machine | `machineId`, `reason` |
| `createWorkOrder` | MANAGER | Machine | `machineId`, `comp`, `type`, `assigneeId`, `failureId`(선택), `inspectionRequestId`(선택) |
| `completeReplacement` | MECHANIC (담당자만) | WorkOrder | `workOrderId` |
| `restartMachine` | MANAGER | Machine | `machineId` |

### 예시: 데모 2번 흐름 (설비 17)

설비 17은 2015-11-27 comp2 고장의 교체 기록을 데모용으로 숨겨서, 기준 시각에 `DOWN`이다.

**① 관리자가 바로 재가동 → 409**

```json
// POST /actions/restartMachine  (X-User-Id: mgr1)
{"machineId": 17}

// 409
{
  "result": "DENIED", "stage": "PRECONDITION", "reasonCode": "UNRESOLVED_FAILURE",
  "message": "미해결 고장 1건이 남아 있어 재가동할 수 없습니다",
  "violations": [
    {"code": "UNRESOLVED_FAILURE", "detail": {"failures": [{"failureId": 742, "comp": "comp2", "datetime": "2015-11-27T06:00:00"}]}}
  ],
  "actionId": 101
}
```

**② 작업지시 생성 → 201, DOWN → MAINTENANCE**

```json
// POST /actions/createWorkOrder  (X-User-Id: mgr1)
{"machineId": 17, "comp": "comp2", "type": "CORRECTIVE", "assigneeId": "mech1", "failureId": 742}

// 201
{
  "actionId": 102, "action": "createWorkOrder", "result": "ALLOWED", "at": "2016-01-01T06:00:00",
  "actor": {"userId": "mgr1", "role": "MANAGER"},
  "target": {"type": "Machine", "id": 17},
  "created": [{"type": "WorkOrder", "id": 1, "status": "OPEN", "comp": "comp2", "assigneeId": "mech1", "failureId": 742}],
  "changes": [{"type": "Machine", "id": 17, "field": "status", "before": "DOWN", "after": "MAINTENANCE"}],
  "remaining": {"uncoveredFailures": []}
}
```

`failureId`를 보내지 않으면 그 부품의 미해결 고장을 자동으로 연결한다. 근거가 점검 요청이면 `changes`에 InspectionRequest의 `REQUESTED → CONVERTED`가 함께 들어간다.

**③ 정비사가 교체 완료 → 200, MAINTENANCE → READY**

```json
// POST /actions/completeReplacement  (X-User-Id: mech1)
{"workOrderId": 1}

// 200
{
  "actionId": 103, "action": "completeReplacement", "result": "ALLOWED", "at": "2016-01-01T06:00:00",
  "actor": {"userId": "mech1", "role": "MECHANIC"},
  "target": {"type": "WorkOrder", "id": 1},
  "created": [{"type": "Maintenance", "id": 3287, "datetime": "2016-01-01T06:00:00", "machineId": 17, "comp": "comp2", "source": "ACTION", "workOrderId": 1}],
  "changes": [
    {"type": "WorkOrder", "id": 1, "field": "status", "before": "OPEN", "after": "DONE"},
    {"type": "FailureEvent", "id": 742, "field": "isResolved", "before": false, "after": true},
    {"type": "Machine", "id": 17, "field": "status", "before": "MAINTENANCE", "after": "READY"}
  ],
  "remaining": {"unresolvedFailures": [], "openWorkOrders": []}
}
```

교체 기록은 `ops.maintenance_log`에 한 행 추가될 뿐이다. 고장의 `isResolved`는 resolvedBy 링크가 새 교체 기록을 찾아서 자동으로 바뀐다.

**④ 관리자가 재가동 → 200, READY → RUNNING**

```json
// POST /actions/restartMachine  (X-User-Id: mgr1)
{"machineId": 17}

// 200
{
  "actionId": 104, "action": "restartMachine", "result": "ALLOWED", "at": "2016-01-01T06:00:00",
  "actor": {"userId": "mgr1", "role": "MANAGER"},
  "target": {"type": "Machine", "id": 17},
  "created": [],
  "changes": [
    {"type": "Machine", "id": 17, "field": "lastRestartAt", "before": null, "after": "2016-01-01T06:00:00"},
    {"type": "Machine", "id": 17, "field": "lastRestartBy", "before": null, "after": "mgr1"},
    {"type": "Machine", "id": 17, "field": "lastRestartWorkOrderId", "before": null, "after": 1},
    {"type": "Machine", "id": 17, "field": "status", "before": "READY", "after": "RUNNING"}
  ],
  "remaining": {}
}
```

### 예시: 권한 거부 (데모 2번 첫 장면)

```json
// POST /actions/restartMachine  (X-User-Id: fw1)
{"machineId": 17}

// 403
{
  "result": "DENIED", "stage": "PERMISSION", "reasonCode": "ROLE_NOT_ALLOWED",
  "message": "이 역할은 이 액션을 실행할 수 없습니다",
  "violations": [{"code": "ROLE_NOT_ALLOWED", "detail": {"role": "FIELD_WORKER", "allowedRoles": ["MANAGER"]}}],
  "actionId": 100
}
```

## 4. 감사 로그 API

```
GET /audit?targetType=Machine&targetId=17
```

```json
{
  "total": 5,
  "items": [
    {
      "actionId": 100, "action": "restartMachine", "result": "DENIED",
      "stage": "PERMISSION", "reasonCode": "ROLE_NOT_ALLOWED",
      "actor": {"userId": "fw1", "role": "FIELD_WORKER"},
      "target": {"type": "Machine", "id": 17},
      "input": {"machineId": 17},
      "at": "2016-01-01T06:00:00",
      "recordedAt": "2026-11-20T14:03:12"
    }
  ]
}
```

- `at`은 가상 시계 시각, `recordedAt`은 실제로 기록한 시각이다.
- 필터: `targetType`, `targetId`, `actorId`, `action`, `result`
- 감사 로그는 추가만 된다. 수정, 삭제 API는 없다.
