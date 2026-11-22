-- 여러 테이블을 합친 읽기용 뷰. 파생 속성(status, riskLevel 등)은 B의 조회 엔진이 계산한다

-- 원본과 액션 기록을 합친 교체 기록. 데모용 숨김 기록은 뺀다 (결정 6, 14)
CREATE VIEW core.maintenance AS
SELECT m.maint_id, m.datetime, m.machine_id, m.comp, 'RAW' AS source, NULL::int AS work_order_id
FROM raw.maintenance m
WHERE NOT EXISTS (SELECT 1 FROM ref.hidden_maintenance h WHERE h.maint_id = m.maint_id)
UNION ALL
SELECT maint_id, datetime, machine_id, comp, 'ACTION', work_order_id
FROM ops.maintenance_log;

-- 설비 + 벤더 + 재가동 기록 (결정 5, 12)
CREATE VIEW core.machine AS
SELECT m.machine_id, m.model, m.age, vm.vendor_id, s.last_restart_at, s.last_restart_by
FROM raw.machines m
JOIN raw.vendor_models vm USING (model)
LEFT JOIN ops.machine_state s USING (machine_id);
