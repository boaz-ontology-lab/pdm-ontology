"""app/db.py의 접속 설정 우선순위. 환경 변수 > infra/.env > compose 기본값."""

from __future__ import annotations

from app import db


def _env_file(tmp_path, text: str):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_match_compose(tmp_path):
    assert db.settings({}, tmp_path / "missing.env") == {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "pdm",
        "POSTGRES_USER": "pdm",
        "POSTGRES_PASSWORD": "pdm",
    }


def test_env_file_overrides_defaults(tmp_path):
    path = _env_file(
        tmp_path,
        "# 주석\n\nPOSTGRES_PORT=5433\nexport POSTGRES_DB='pdm_dev'\nPOSTGRES_USER=\nNOT_A_LINE\n",
    )
    s = db.settings({}, path)
    assert s["POSTGRES_PORT"] == "5433"
    assert s["POSTGRES_DB"] == "pdm_dev"
    assert s["POSTGRES_USER"] == "pdm"  # 빈 값은 기본값 (compose의 ${X:-기본값}과 같다)


def test_environment_wins(tmp_path):
    """컨테이너에서는 compose가 넣은 환경 변수가 파일보다 앞선다."""
    path = _env_file(tmp_path, "POSTGRES_HOST=localhost\nPOSTGRES_PORT=5433\n")
    s = db.settings({"POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432"}, path)
    assert (s["POSTGRES_HOST"], s["POSTGRES_PORT"]) == ("postgres", "5432")
    s = db.settings({"POSTGRES_PORT": ""}, path)  # 빈 환경 변수는 없는 것으로 본다
    assert s["POSTGRES_PORT"] == "5433"


def test_env_example_parses(tmp_path):
    """infra/.env.example을 그대로 복사해 써도 기본값과 같다."""
    example = db.ENV_FILE.with_name(".env.example")
    assert db.settings({}, example) == db.settings({}, tmp_path / "missing.env")
