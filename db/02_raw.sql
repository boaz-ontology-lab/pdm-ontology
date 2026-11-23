-- Azure PdM 원본 CSV 5개. 컬럼은 snake_case로 바꾸고, 두 개는 온톨로지 이름에 맞춘다
--   errorID -> error_type, failure -> comp
-- 에러, 고장, 정비에는 PK가 없어서 CSV 파일 순서대로 1부터 번호를 붙인다 (docs/synthetic-data.md 1절)

CREATE TABLE raw.machines (
    machine_id int  PRIMARY KEY,
    model      text NOT NULL,
    age        int  NOT NULL
);

CREATE TABLE raw.telemetry (
    datetime   timestamp NOT NULL,
    machine_id int       NOT NULL REFERENCES raw.machines,
    volt       double precision,
    rotate     double precision,
    pressure   double precision,
    vibration  double precision,
    PRIMARY KEY (machine_id, datetime)
);

CREATE TABLE raw.errors (
    error_event_id int       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    datetime       timestamp NOT NULL,
    machine_id     int       NOT NULL REFERENCES raw.machines,
    error_type     text      NOT NULL
);

CREATE TABLE raw.failures (
    failure_id int       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    datetime   timestamp NOT NULL,
    machine_id int       NOT NULL REFERENCES raw.machines,
    comp       text      NOT NULL
);

CREATE TABLE raw.maintenance (
    maint_id   int       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    datetime   timestamp NOT NULL,
    machine_id int       NOT NULL REFERENCES raw.machines,
    comp       text      NOT NULL
);

CREATE INDEX ON raw.errors (machine_id, datetime);
CREATE INDEX ON raw.failures (machine_id, comp, datetime);
CREATE INDEX ON raw.maintenance (machine_id, comp, datetime);
