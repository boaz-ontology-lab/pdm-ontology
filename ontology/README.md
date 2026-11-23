# ontology

온톨로지 스펙이 모이는 곳이다. 엔진은 이 YAML만 읽고 동작한다.

| 파일 | 내용 | 담당 |
|---|---|---|
| `objects.yaml` | 객체 타입, 속성, 파생 속성 | B |
| `links.yaml` | 객체 사이의 링크 | B |
| `actions.yaml` | 액션, 권한, 사전조건, 거부 코드 | C |

이름과 코드 값은 [표기 규칙](../docs/conventions.md)을 따르고, CI가 검사한다.
YAML의 예약 키(`source`, `properties`, `allowed_roles` 등)는 snake_case로 쓰고,
우리가 붙이는 이름(객체, 속성, 링크, 액션)은 규칙에 맞춰 쓴다.

## 형식 예시

```yaml
# objects.yaml
Machine:
  source: core.machine          # 바라보는 테이블이나 뷰
  primary_key: machineId
  properties:
    machineId: { column: machine_id, type: int }
    model: { column: model, type: string }
    lastRestartAt: { column: last_restart_at, type: datetime }
  derived:
    hasOpenFailure:
      type: bool
      description: "미해결 고장이 하나라도 있다"
      sql: >-
        EXISTS (SELECT 1 FROM {FailureEvent} f
                WHERE f.machine_id = {machineId} AND NOT {f.isResolved})
    errorCount7d:
      type: int
      description: "최근 7일 동안의 에러 건수"
      sql: >-
        (SELECT count(*) FROM {ErrorEvent} e
         WHERE e.machine_id = {machineId}
           AND e.datetime > {now} - interval '7 days' AND e.datetime <= {now})
```

파생 속성은 SQL 식으로 적는다. `{machineId}`는 이 객체의 속성, `{FailureEvent} f`는 그 객체의 source,
`{f.isResolved}`는 다른 객체의 파생 속성, `{now}`는 가상 시계 시각이다. 자세한 규칙은 `objects.yaml` 맨 위 주석을 본다.

```yaml
# links.yaml
hasErrors:
  from: Machine
  to: ErrorEvent
  cardinality: one_to_many
  join:
    - machineId = machineId

resolvedBy:
  from: FailureEvent
  to: Maintenance
  cardinality: many_to_zero_or_one
  join:
    - machineId = machineId
    - comp = comp
    - to.datetime >= from.datetime
  pick: earliest(to.datetime)
```

```yaml
# actions.yaml
reason_codes:
  ROLE_NOT_ALLOWED: { stage: PERMISSION, message: "이 역할은 이 액션을 실행할 수 없습니다" }

restartMachine:
  target: Machine
  allowed_roles: [MANAGER]
  input:
    machineId: { type: int, required: true }
```

객체와 링크 전체 모습은 [다이어그램](../docs/diagrams/ontology.png)을 본다.
