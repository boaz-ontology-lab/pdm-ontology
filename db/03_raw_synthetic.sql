-- 합성 원천 데이터. 값은 data/load.py가 넣는다 (docs/synthetic-data.md 2절)

CREATE TABLE raw.vendors (
    vendor_id int  PRIMARY KEY,
    name      text NOT NULL
);

-- 모델 하나는 벤더 하나에만 속한다 (결정 5)
CREATE TABLE raw.vendor_models (
    model     text PRIMARY KEY,
    vendor_id int  NOT NULL REFERENCES raw.vendors
);

CREATE TABLE raw.users (
    user_id   text PRIMARY KEY,
    name      text NOT NULL,
    role      text NOT NULL CHECK (role IN ('FIELD_WORKER', 'MECHANIC', 'MANAGER', 'VENDOR')),
    vendor_id int  REFERENCES raw.vendors,
    -- vendor_id는 VENDOR 역할만 가진다
    CHECK ((role = 'VENDOR') = (vendor_id IS NOT NULL))
);

CREATE TABLE raw.components (
    comp text PRIMARY KEY,
    name text NOT NULL
);
