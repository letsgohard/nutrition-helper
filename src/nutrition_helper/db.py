from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nutrition_helper.config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL UNIQUE,
    display_name TEXT,
    sex TEXT,
    age INTEGER,
    height_cm REAL,
    weight_kg REAL,
    activity_level TEXT,
    primary_goal TEXT,
    concerns_text TEXT,
    fat_insights_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(telegram_chat_id) REFERENCES users(telegram_chat_id)
);

CREATE TABLE IF NOT EXISTS goal_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL,
    goal_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    note TEXT,
    FOREIGN KEY(telegram_chat_id) REFERENCES users(telegram_chat_id)
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_text TEXT,
    image_path TEXT,
    meal_type TEXT NOT NULL,
    portion_label TEXT,
    confidence REAL,
    total_calories REAL NOT NULL DEFAULT 0,
    total_protein REAL NOT NULL DEFAULT 0,
    total_fat REAL NOT NULL DEFAULT 0,
    total_carbs REAL NOT NULL DEFAULT 0,
    total_fiber REAL NOT NULL DEFAULT 0,
    total_saturated_fat REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    estimated_amount TEXT,
    amount_unit TEXT,
    estimated_grams REAL,
    calories REAL NOT NULL DEFAULT 0,
    protein REAL NOT NULL DEFAULT 0,
    fat REAL NOT NULL DEFAULT 0,
    carbs REAL NOT NULL DEFAULT 0,
    fiber REAL NOT NULL DEFAULT 0,
    saturated_fat REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(meal_id) REFERENCES meals(id)
);

CREATE TABLE IF NOT EXISTS wellbeing_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL,
    meal_id INTEGER,
    satiety INTEGER,
    energy INTEGER,
    heaviness INTEGER,
    bloating INTEGER,
    mood INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(meal_id) REFERENCES meals(id)
);

CREATE TABLE IF NOT EXISTS ai_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL,
    input_type TEXT NOT NULL,
    source_text TEXT,
    model_output_json TEXT,
    created_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_local_str() -> str:
    return date.today().isoformat()


@dataclass
class ProfileInput:
    display_name: str
    sex: str
    age: int
    height_cm: float
    weight_kg: float
    activity_level: str
    primary_goal: str
    concerns_text: str
    fat_insights_enabled: bool


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def upsert_user(self, telegram_chat_id: int, username: str | None, first_name: str | None) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO users (telegram_chat_id, username, first_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id)
                DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = excluded.updated_at
                """,
                (telegram_chat_id, username, first_name, now, now),
            )
            conn.commit()

    def get_profile(self, telegram_chat_id: int) -> sqlite3.Row | None:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT display_name, sex, age, height_cm, weight_kg, activity_level,
                       primary_goal, concerns_text, fat_insights_enabled, updated_at
                FROM user_profiles
                WHERE telegram_chat_id = ?
                """,
                (telegram_chat_id,),
            ).fetchone()

    def save_profile(self, telegram_chat_id: int, profile: ProfileInput) -> None:
        now = utc_now_iso()
        existing = self.get_profile(telegram_chat_id)
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (
                    telegram_chat_id,
                    display_name,
                    sex,
                    age,
                    height_cm,
                    weight_kg,
                    activity_level,
                    primary_goal,
                    concerns_text,
                    fat_insights_enabled,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id)
                DO UPDATE SET
                    display_name = excluded.display_name,
                    sex = excluded.sex,
                    age = excluded.age,
                    height_cm = excluded.height_cm,
                    weight_kg = excluded.weight_kg,
                    activity_level = excluded.activity_level,
                    primary_goal = excluded.primary_goal,
                    concerns_text = excluded.concerns_text,
                    fat_insights_enabled = excluded.fat_insights_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_chat_id,
                    profile.display_name,
                    profile.sex,
                    profile.age,
                    profile.height_cm,
                    profile.weight_kg,
                    profile.activity_level,
                    profile.primary_goal,
                    profile.concerns_text,
                    int(profile.fat_insights_enabled),
                    now,
                    now,
                ),
            )
            if existing and existing["primary_goal"] != profile.primary_goal:
                conn.execute(
                    """
                    UPDATE goal_periods
                    SET ended_at = ?
                    WHERE telegram_chat_id = ? AND ended_at IS NULL
                    """,
                    (now, telegram_chat_id),
                )
                conn.execute(
                    """
                    INSERT INTO goal_periods (telegram_chat_id, goal_type, started_at, note)
                    VALUES (?, ?, ?, ?)
                    """,
                    (telegram_chat_id, profile.primary_goal, now, "goal updated"),
                )
            elif not existing:
                conn.execute(
                    """
                    INSERT INTO goal_periods (telegram_chat_id, goal_type, started_at, note)
                    VALUES (?, ?, ?, ?)
                    """,
                    (telegram_chat_id, profile.primary_goal, now, "initial goal"),
                )
            conn.commit()

    def create_meal(
        self,
        telegram_chat_id: int,
        day: str,
        source_type: str,
        source_text: str | None,
        image_path: str | None,
        meal_type: str,
        portion_label: str,
        confidence: float,
        items: list[dict[str, float | str | None]],
    ) -> int:
        totals = {
            "calories": 0.0,
            "protein": 0.0,
            "fat": 0.0,
            "carbs": 0.0,
            "fiber": 0.0,
            "saturated_fat": 0.0,
        }
        for item in items:
            totals["calories"] += float(item.get("calories") or 0.0)
            totals["protein"] += float(item.get("protein") or 0.0)
            totals["fat"] += float(item.get("fat") or 0.0)
            totals["carbs"] += float(item.get("carbs") or 0.0)
            totals["fiber"] += float(item.get("fiber") or 0.0)
            totals["saturated_fat"] += float(item.get("saturated_fat") or 0.0)

        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO meals (
                    telegram_chat_id,
                    day,
                    source_type,
                    source_text,
                    image_path,
                    meal_type,
                    portion_label,
                    confidence,
                    total_calories,
                    total_protein,
                    total_fat,
                    total_carbs,
                    total_fiber,
                    total_saturated_fat,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_chat_id,
                    day,
                    source_type,
                    source_text,
                    image_path,
                    meal_type,
                    portion_label,
                    confidence,
                    totals["calories"],
                    totals["protein"],
                    totals["fat"],
                    totals["carbs"],
                    totals["fiber"],
                    totals["saturated_fat"],
                    utc_now_iso(),
                ),
            )
            meal_id = int(cursor.lastrowid)
            for item in items:
                conn.execute(
                    """
                    INSERT INTO meal_items (
                        meal_id,
                        item_name,
                        estimated_amount,
                        amount_unit,
                        estimated_grams,
                        calories,
                        protein,
                        fat,
                        carbs,
                        fiber,
                        saturated_fat
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meal_id,
                        str(item.get("item_name") or "Unknown item"),
                        item.get("estimated_amount"),
                        item.get("amount_unit"),
                        item.get("estimated_grams"),
                        float(item.get("calories") or 0.0),
                        float(item.get("protein") or 0.0),
                        float(item.get("fat") or 0.0),
                        float(item.get("carbs") or 0.0),
                        float(item.get("fiber") or 0.0),
                        float(item.get("saturated_fat") or 0.0),
                    ),
                )
            conn.commit()
            return meal_id

    def get_last_meal(self, telegram_chat_id: int) -> sqlite3.Row | None:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT id, day, source_text, meal_type, total_calories, total_protein, total_fat, total_carbs, created_at
                FROM meals
                WHERE telegram_chat_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (telegram_chat_id,),
            ).fetchone()

    def list_meals_for_day(self, telegram_chat_id: int, day: str) -> list[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT id, source_text, meal_type, portion_label, total_calories, total_protein, total_fat, total_carbs, total_fiber
                FROM meals
                WHERE telegram_chat_id = ? AND day = ?
                ORDER BY id ASC
                """,
                (telegram_chat_id, day),
            ).fetchall()

    def get_today_totals(self, telegram_chat_id: int, day: str) -> sqlite3.Row:
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS meals_count,
                    COALESCE(SUM(total_calories), 0) AS calories,
                    COALESCE(SUM(total_protein), 0) AS protein,
                    COALESCE(SUM(total_fat), 0) AS fat,
                    COALESCE(SUM(total_carbs), 0) AS carbs,
                    COALESCE(SUM(total_fiber), 0) AS fiber,
                    COALESCE(SUM(total_saturated_fat), 0) AS saturated_fat
                FROM meals
                WHERE telegram_chat_id = ? AND day = ?
                """,
                (telegram_chat_id, day),
            ).fetchone()
            assert row is not None
            return row

    def log_wellbeing(
        self,
        telegram_chat_id: int,
        meal_id: int | None,
        satiety: int,
        energy: int,
        heaviness: int,
        bloating: int,
        mood: int,
        note: str | None,
    ) -> int:
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO wellbeing_logs (
                    telegram_chat_id,
                    meal_id,
                    satiety,
                    energy,
                    heaviness,
                    bloating,
                    mood,
                    note,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (telegram_chat_id, meal_id, satiety, energy, heaviness, bloating, mood, note, utc_now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def latest_wellbeing(self, telegram_chat_id: int) -> sqlite3.Row | None:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT satiety, energy, heaviness, bloating, mood, note, created_at
                FROM wellbeing_logs
                WHERE telegram_chat_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (telegram_chat_id,),
            ).fetchone()

    def get_stats_snapshot(self, telegram_chat_id: int, days: int = 7) -> dict[str, object]:
        day_from = (date.today() - timedelta(days=days - 1)).isoformat()
        with closing(self.connect()) as conn:
            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS meals_count,
                    COUNT(DISTINCT day) AS active_days,
                    COALESCE(SUM(total_calories), 0) AS calories,
                    COALESCE(SUM(total_protein), 0) AS protein,
                    COALESCE(SUM(total_fat), 0) AS fat,
                    COALESCE(SUM(total_carbs), 0) AS carbs
                FROM meals
                WHERE telegram_chat_id = ? AND day >= ?
                """,
                (telegram_chat_id, day_from),
            ).fetchone()

            heavy_items = conn.execute(
                """
                SELECT mi.item_name
                FROM wellbeing_logs wl
                JOIN meal_items mi ON mi.meal_id = wl.meal_id
                JOIN meals m ON m.id = wl.meal_id
                WHERE wl.telegram_chat_id = ?
                  AND wl.heaviness >= 4
                  AND m.day >= ?
                """,
                (telegram_chat_id, day_from),
            ).fetchall()

            low_energy_items = conn.execute(
                """
                SELECT mi.item_name
                FROM wellbeing_logs wl
                JOIN meal_items mi ON mi.meal_id = wl.meal_id
                JOIN meals m ON m.id = wl.meal_id
                WHERE wl.telegram_chat_id = ?
                  AND wl.energy <= 2
                  AND m.day >= ?
                """,
                (telegram_chat_id, day_from),
            ).fetchall()

        heavy_counter = Counter(str(row["item_name"]) for row in heavy_items)
        low_energy_counter = Counter(str(row["item_name"]) for row in low_energy_items)

        return {
            "day_from": day_from,
            "meals_count": int(totals["meals_count"] if totals else 0),
            "active_days": int(totals["active_days"] if totals else 0),
            "calories": float(totals["calories"] if totals else 0.0),
            "protein": float(totals["protein"] if totals else 0.0),
            "fat": float(totals["fat"] if totals else 0.0),
            "carbs": float(totals["carbs"] if totals else 0.0),
            "top_heavy_items": [name for name, _ in heavy_counter.most_common(3)],
            "top_low_energy_items": [name for name, _ in low_energy_counter.most_common(3)],
        }

    def log_ai_audit(self, telegram_chat_id: int, input_type: str, source_text: str | None, model_output_json: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO ai_audit_logs (telegram_chat_id, input_type, source_text, model_output_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (telegram_chat_id, input_type, source_text, model_output_json, utc_now_iso()),
            )
            conn.commit()


def build_database(settings: Settings) -> Database:
    return Database(settings.db_path)
