-- 액션이 쓰는 데이터. 처음에는 0건이다 (docs/synthetic-data.md 3절)
-- 데모 초기화는 ops를 비우거나 load.py를 다시 실행한다 (결정 6)
-- 외래 키 때문에 inspection_requests → work_orders → maintenance_log 순서로 만든다

CREATE TABLE ops.inspection_requests (
    inspection_request_id int       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine_id            int       NOT NULL REFERENCES raw.machines,
    requested_by          text      NOT NULL REFERENCES raw.users,
    reason                text      NOT NULL CHECK (length(reason) <= 500),
    status                text      NOT NULL CHECK (status IN ('REQUESTED', 'CONVERTED', 'CLOSED')),
    created_at            timestamp NOT NULL,
    evidence              jsonb     NOT NULL  -- 요청 시점의 errorCount7d, riskLevel
);
-- DUPLICATE_REQUEST의 DB 쪽 안전장치: 설비마다 REQUESTED는 하나
CREATE UNIQUE INDEX ON ops.inspection_requests (machine_id) WHERE status = 'REQUESTED';

CREATE TABLE ops.work_orders (
    work_order_id         int       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine_id            int       NOT NULL REFERENCES raw.machines,
    comp                  text      NOT NULL REFERENCES raw.components,
    type                  text      NOT NULL CHECK (type IN ('CORRECTIVE', 'PREVENTIVE')),
    status                text      NOT NULL CHECK (status IN ('OPEN', 'DONE', 'CANCELED')),
    assignee_id           text      NOT NULL REFERENCES raw.users,
    created_by            text      NOT NULL REFERENCES raw.users,
    created_at            timestamp NOT NULL,
    completed_at          timestamp,
    failure_id            int       REFERENCES raw.failures,             -- addresses 링크
    inspection_request_id int       REFERENCES ops.inspection_requests,  -- basedOn 링크
    CHECK ((status = 'DONE') = (completed_at IS NOT NULL)),
    -- CORRECTIVE는 항상 고장을 잇고, PREVENTIVE는 잇지 않는다
    CHECK ((type = 'CORRECTIVE') = (failure_id IS NOT NULL))
);
-- OPEN_WORK_ORDER의 DB 쪽 안전장치: 같은 설비, 같은 부품에 OPEN은 하나
CREATE UNIQUE INDEX ON ops.work_orders (machine_id, comp) WHERE status = 'OPEN';

-- 교체 완료 액션이 쓰는 교체 기록. 원본 번호(1~3286) 뒤로 이어서 붙인다
CREATE TABLE ops.maintenance_log (
    maint_id      int       GENERATED ALWAYS AS IDENTITY (START WITH 3287) PRIMARY KEY,
    datetime      timestamp NOT NULL,
    machine_id    int       NOT NULL REFERENCES raw.machines,
    comp          text      NOT NULL REFERENCES raw.components,
    work_order_id int       NOT NULL UNIQUE REFERENCES ops.work_orders  -- completedAs 1:0..1
);

-- 재가동 기록. 첫 재가동 때 행이 생긴다. status는 저장하지 않는다 (결정 12)
CREATE TABLE ops.machine_state (
    machine_id      int       PRIMARY KEY REFERENCES raw.machines,
    last_restart_at timestamp NOT NULL,
    last_restart_by text      NOT NULL REFERENCES raw.users
);

-- 감사 로그. 호출마다 한 행, 추가만 허용한다 (거부, 오류도 남긴다)
CREATE TABLE ops.audit_log (
    action_id   int         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action      text        NOT NULL,
    result      text        NOT NULL CHECK (result IN ('ALLOWED', 'DENIED', 'ERROR')),
    stage       text        CHECK (stage IN ('AUTH', 'INPUT', 'PERMISSION', 'PRECONDITION')),
    reason_code text,
    actor_id    text,        -- FK 없음: UNKNOWN_USER면 헤더 값 그대로이거나 NULL
    actor_role  text,
    target_type text,
    target_id   int,
    input       jsonb       NOT NULL,
    response    jsonb,       -- 응답 본문 전체 (violations, created, changes)
    at          timestamp   NOT NULL,               -- 가상 시계 시각
    recorded_at timestamptz NOT NULL DEFAULT now(), -- 실제로 기록한 시각
    CHECK ((result = 'ALLOWED') = (stage IS NULL))
);
CREATE INDEX ON ops.audit_log (target_type, target_id);

-- TRUNCATE는 막지 않는다. 데모 초기화 때 ops를 비워야 한다
CREATE FUNCTION ops.reject_audit_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ops.audit_log은 추가만 할 수 있습니다';
END $$;

CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON ops.audit_log
    FOR EACH ROW EXECUTE FUNCTION ops.reject_audit_change();
