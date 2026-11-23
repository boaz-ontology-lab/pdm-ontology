-- 팀이 판단해 덧붙인 데이터. 값은 data/load.py가 넣는다 (docs/synthetic-data.md 2절)

-- 에러가 가리키는 부품 (indicates 링크, riskLevel HIGH 판정)
CREATE TABLE ref.error_component_map (
    error_type text PRIMARY KEY,
    comp       text NOT NULL REFERENCES raw.components
);

-- 데모용으로 "없는 것"으로 보는 원본 교체 기록 (결정 14). core.maintenance 뷰가 뺀다
CREATE TABLE ref.hidden_maintenance (
    maint_id int PRIMARY KEY REFERENCES raw.maintenance
);
