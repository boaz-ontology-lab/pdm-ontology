# 합성 데이터

원본 Azure PdM에는 설비와 이벤트만 있고 사람, 조직, 업무 데이터가 없다.
데모에 필요한 것만 만들어 원본과 키로 연결한다. 원본은 고치지 않는다.

- 시드: 42 (누가 실행해도 같은 결과)
- 기준 시각: 2016-01-01 06:00 (결정 7). 합성 데이터의 모든 시각은 이보다 앞선다

## 1. 원본에 붙이는 번호

에러, 고장, 정비 CSV에는 PK가 없어서 적재할 때 1부터 번호를 붙인다 (결정 3).
**CSV 파일에 적힌 순서대로** 붙인다. 파일은 설비 번호 → 시각 순으로 정렬되어 있다.
번호 순서는 시간 순서가 아니므로, 시간 정렬은 항상 `datetime`으로 한다.

| 테이블 | 번호 | 범위 |
|---|---|---|
| `raw.errors` | `error_event_id` | 1 ~ 3919 |
| `raw.failures` | `failure_id` | 1 ~ 761 |
| `raw.maintenance` | `maint_id` | 1 ~ 3286 |

교체 완료 액션이 만드는 교체 기록(`ops.maintenance_log`)은 **3287번부터 이어서** 붙인다.
`core.maintenance` 뷰에서 원본과 합쳐도 `maint_id`가 겹치지 않는다.
번호는 DDL에서 `GENERATED ALWAYS AS IDENTITY (START WITH 3287)`로 강제한다. 앱 코드가 번호를 넣지 않는다.
`core.maintenance` 뷰는 원본 행에 `source = 'RAW'`, `ops.maintenance_log` 행에 `source = 'ACTION'`을 붙인다.

## 2. 값을 채우는 테이블

### raw.vendors (2행)

| vendor_id | name |
|---|---|
| 1 | Vendor A |
| 2 | Vendor B |

### raw.vendor_models (4행)

모델 하나는 벤더 하나에만 속한다 (결정 5).

| model | vendor_id | 설비 수 |
|---|---|---|
| model1 | 1 | 16 |
| model2 | 1 | 17 |
| model3 | 2 | 35 |
| model4 | 2 | 32 |

벤더 A 33대, 벤더 B 67대. 데모 설비 17은 model1(벤더 A), 88은 model4(벤더 B).

### raw.users (8행)

| user_id | role | vendor_id |
|---|---|---|
| fw1, fw2 | FIELD_WORKER | |
| mech1, mech2, mech3 | MECHANIC | |
| mgr1 | MANAGER | |
| venA | VENDOR | 1 |
| venB | VENDOR | 2 |

- `user_id`는 C의 테스트 fixture와 같은 형식이다. 사용자 ID만 문자열이다 (결정 17)
- `name`은 Faker(`ko_KR`, 시드 42)로 만든다. 권한 규칙에서는 쓰지 않는 표시용 값이다
- `vendor_id`는 VENDOR 역할만 가진다

### raw.components (4행)

| comp | name |
|---|---|
| comp1 | comp1 |
| comp2 | comp2 |
| comp3 | comp3 |
| comp4 | comp4 |

원본에 부품 이름이 없어서 표시용 `name`도 `comp` 값을 그대로 쓴다.

### ref.error_component_map (5행)

에러가 어느 부품의 이상을 가리키는지 (indicates 링크, riskLevel HIGH 판정에 사용).
C의 EDA 기준: 대응 에러는 고장 정확히 24시간 전에 찍혔다.

| error_type | comp |
|---|---|
| error1 | comp1 |
| error2 | comp2 |
| error3 | comp2 |
| error4 | comp3 |
| error5 | comp4 |

### ref.hidden_maintenance (3행)

데모용 미해결 고장을 만들기 위해 짝인 원본 교체 기록을 "없는 것"으로 본다 (결정 14).
원본은 지우지 않고, `core.maintenance` 뷰가 이 목록을 뺀다.

| maint_id | 짝인 고장 | 용도 |
|---|---|---|
| 564 | failure_id 121 (설비 17, 2015-11-27, comp2) | 데모 2번 |
| 2898 | failure_id 648 (설비 88, 2015-11-30, comp2) | 동시 고장 테스트 |
| 2899 | failure_id 649 (설비 88, 2015-11-30, comp4) | 동시 고장 테스트 |

숨긴 기록 이후에도 설비 17·88에는 원본의 에러, 교체, 고장 기록이 남아 있다.
데모용으로 교체 기록 하나만 숨겼기 때문이다. 발표에서 이력을 보여 줄 때는
"교체 기록(설비 17은 comp2, 88은 comp2·comp4)을 일부러 숨겨 미해결 상태를 만들었다"고 설명한다.

| 설비 | 숨긴 고장 이후 원본 기록 |
|---|---|
| 17 | 12-12 comp3 교체, 12-26 error1, 12-27 comp1 고장·교체 |
| 88 | 12-03 error4, 12-05 error3, 12-28 error2, 12-29 error1, 12-30 comp1 고장·교체 |

## 3. 비워 두는 테이블 (액션이 채움)

처음에는 0건이다. 데모 초기화는 ops 테이블을 비우는 것으로 끝난다 (결정 6).
컬럼 상세는 D4에 C와 맞춘다.

| 테이블 | 컬럼 | 채우는 액션 |
|---|---|---|
| `ops.machine_state` | machine_id, last_restart_at, last_restart_by, last_restart_work_order_id | 재가동. 처음 재가동할 때 행이 생기고, 이후엔 값만 바뀐다 (결정 12, 21) |
| `ops.work_orders` | work_order_id, machine_id, comp, type, status, assignee_id, created_by, created_at, completed_at, failure_id, inspection_request_id | 작업지시 생성, 교체 완료 |
| `ops.inspection_requests` | inspection_request_id, machine_id, requested_by, reason, status, created_at, evidence | 점검 요청 |
| `ops.maintenance_log` | maint_id(3287부터), datetime, machine_id, comp, work_order_id | 교체 완료 |
| `ops.audit_log` | C가 정한다 | 모든 액션 |

## 4. 만들지 않는 것

- `Machine.status`, `FailureEvent.isResolved`, `riskLevel`: 저장하지 않고 조회할 때 계산한다 (결정 11, 12)
- 짝 없는 고장 18건의 합성 교체 기록: resolvedBy 규칙으로 해결된다 (결정 4)
