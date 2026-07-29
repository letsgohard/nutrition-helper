# nutrition-helper

`nutrition-helper` is a Telegram bot for:

- food logging by text and photo;
- rough KBJU estimates;
- lightweight wellbeing journaling;
- daily totals and simple food-to-feelings patterns.

## Current scope

The current scaffold includes:

- onboarding and user profile capture;
- text meal logging;
- photo meal logging hook;
- simplified portion confirmation;
- wellbeing logging;
- daily summary and basic weekly stats;
- OpenAI-backed meal parsing with a local fallback.

## Run locally

1. Create `.env` from `.env.example`
2. Install dependencies
3. Run:

```bash
PYTHONPATH=src python -m nutrition_helper.main
```

## Deploy on Railway

The easiest path for this bot is Railway with a persistent volume.

### Why this setup

- the bot keeps running when your laptop is off;
- SQLite, runtime state, and uploaded files survive restarts;
- no code rewrite to Postgres is required right now.

### Files already prepared

- `Dockerfile` for container deployment;
- `.dockerignore` to keep local secrets and data out of the image.

### Railway steps

1. Push this folder to a GitHub repository.
2. In Railway, create a new project from that GitHub repo.
3. Add one volume and mount it at `/data`.
4. Add these environment variables in Railway:

```env
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
APP_TIMEZONE=Europe/Moscow
DB_PATH=/data/nutrition_helper.sqlite3
STATE_PATH=/data/runtime_state.json
UPLOADS_DIR=/data/uploads
ASSISTANT_NAME=BiteNote
DEFAULT_PORTION_SMALL_FACTOR=0.8
DEFAULT_PORTION_LARGE_FACTOR=1.25
WELLBEING_FOLLOWUP_MINUTES=90
```

5. Deploy.

### Notes

- Do not upload your local `.env` file to GitHub.
- If the bot token or OpenAI key were shared in chat, rotate them before production deploy.
- SQLite is fine for a personal MVP. If you later want multiple workers or more users, we should move to Postgres.
