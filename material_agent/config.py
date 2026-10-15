"""실습 설정(기준시각·도구 실행 한도)과 API 키를 읽는다."""
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from material_agent.data import DATA_DIR, PROJECT_DIR, parse_time

KEY_NAME = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class Settings:
    base_time: datetime
    max_tool_calls: int


def load_settings(data_dir: Path = DATA_DIR) -> Settings:
    raw = json.loads((data_dir / "실습설정.json").read_text(encoding="utf-8-sig"))
    return Settings(parse_time(raw["기준시각"]), int(raw["최대도구실행횟수"]))


def load_api_key(project_dir: Path = PROJECT_DIR) -> str | None:
    """환경변수를 먼저 보고, 없으면 프로젝트 폴더의 .env에서 찾는다."""
    if os.environ.get(KEY_NAME):
        return os.environ[KEY_NAME]
    env_file = project_dir / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_NAME and value.strip():
            return value.strip().strip('"').strip("'")
    return None
