.PHONY: setup lint format test conventions check up up-db down om-ingest om-tags om-tags-push

setup:        ## 의존성 설치
	uv sync

lint:         ## 린트와 형식 검사
	uv run ruff check .
	uv run ruff format --check .

format:       ## 자동 수정
	uv run ruff check --fix .
	uv run ruff format .

test:         ## 테스트
	uv run pytest

conventions:  ## ontology/*.yaml 표기 규칙 검사
	uv run python scripts/check_conventions.py ontology

check: lint test conventions  ## PR 올리기 전에 CI와 같은 검사

up:           ## Postgres + OpenMetadata 실행 (OpenMetadata UI: http://localhost:8585)
	docker compose -f infra/docker-compose.yml up -d

up-db:        ## Postgres만 실행 (OpenMetadata 없이, 조회·액션 개발용)
	docker compose -f infra/docker-compose.yml up -d postgres

down:         ## 전체 중지
	docker compose -f infra/docker-compose.yml down

om-ingest:    ## OpenMetadata에 pdm DB 수집 (make up과 data/load.py 다음에)
	uv run python scripts/om_ingest.py

om-tags:      ## OpenMetadata의 PdmPolicy 태그를 ontology/policy_tags.yaml로 받기
	uv run python scripts/sync_policy_tags.py

om-tags-push: ## ontology/policy_tags.yaml의 태그를 OpenMetadata에 붙이기 (om-ingest 다음에)
	uv run python scripts/sync_policy_tags.py --push
