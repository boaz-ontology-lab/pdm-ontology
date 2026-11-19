.PHONY: setup lint format test conventions check

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
