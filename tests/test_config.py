"""설정·API 키 읽기 테스트."""
from datetime import datetime

from material_agent.config import load_api_key, load_settings
from material_agent.data import KST


def test_load_settings_defaults():
    s = load_settings()
    assert s.base_time == datetime(2026, 10, 15, 9, tzinfo=KST) and s.max_tool_calls == 16


def test_api_key_env_wins(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-file", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert load_api_key(tmp_path) == "sk-env"


def test_api_key_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-file\n", encoding="utf-8")
    assert load_api_key(tmp_path) == "sk-file"


def test_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_api_key(tmp_path) is None
