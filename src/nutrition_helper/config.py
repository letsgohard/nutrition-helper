from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    openai_model: str
    openai_transcribe_model: str
    app_timezone: str
    db_path: Path
    state_path: Path
    uploads_dir: Path
    assistant_name: str
    default_portion_small_factor: float
    default_portion_large_factor: float
    wellbeing_followup_minutes: int


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        openai_transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        app_timezone=os.getenv("APP_TIMEZONE", "Europe/Moscow"),
        db_path=Path(os.getenv("DB_PATH", "./data/nutrition_helper.sqlite3")),
        state_path=Path(os.getenv("STATE_PATH", "./data/runtime_state.json")),
        uploads_dir=Path(os.getenv("UPLOADS_DIR", "./data/uploads")),
        assistant_name=os.getenv("ASSISTANT_NAME", "nutrition-helper"),
        default_portion_small_factor=float(os.getenv("DEFAULT_PORTION_SMALL_FACTOR", "0.8")),
        default_portion_large_factor=float(os.getenv("DEFAULT_PORTION_LARGE_FACTOR", "1.25")),
        wellbeing_followup_minutes=int(os.getenv("WELLBEING_FOLLOWUP_MINUTES", "90")),
    )
