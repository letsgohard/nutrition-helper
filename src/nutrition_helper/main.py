from __future__ import annotations

from nutrition_helper.ai import AIService
from nutrition_helper.bot import build_application
from nutrition_helper.config import load_settings
from nutrition_helper.db import build_database


def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and fill it.")

    db = build_database(settings)
    db.init()
    ai = AIService(settings)
    application = build_application(settings, db, ai)
    application.run_polling()


if __name__ == "__main__":
    main()
