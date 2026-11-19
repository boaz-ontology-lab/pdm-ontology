# db

PostgreSQL 스키마, 뷰, 권한 SQL을 둔다. 담당은 A.

| 스키마 | 담는 것 | 앱 권한 |
|---|---|---|
| `raw` | 원본 CSV와 합성 원천 데이터 (users, vendors, vendor_models, components) | 읽기만 |
| `ref` | 팀이 판단해 덧붙인 데이터 (error_component_map, 데모용 숨김 목록) | 읽기만 |
| `ops` | 액션이 쓰는 데이터 (work_orders, inspection_requests, maintenance_log, machine_state, audit_log) | 읽기, 쓰기 |
| `core` | 여러 테이블을 합친 뷰 (maintenance, machine) | 읽기만 |

파일은 실행 순서대로 번호를 붙인다. 예: `01_schemas.sql`, `02_raw.sql`, `10_core_views.sql`, `20_grants.sql`

이름 규칙은 [표기 규칙](../docs/conventions.md#4-db)을 따른다.
