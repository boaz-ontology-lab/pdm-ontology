-- 스키마를 지우고 다시 만든다. data/load.py가 매번 처음부터 실행한다 (ops의 액션 기록도 지워진다)
-- 스키마 역할은 db/README.md 참고
DROP SCHEMA IF EXISTS core, ops, ref, raw CASCADE;

CREATE SCHEMA raw;
CREATE SCHEMA ref;
CREATE SCHEMA ops;
CREATE SCHEMA core;
