from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from nutrition_helper.ai import AIService, MealAnalysis
from nutrition_helper.config import Settings
from nutrition_helper.db import Database, ProfileInput, today_local_str
from nutrition_helper.storage import load_state, save_state

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["Добавить еду", "Как я себя чувствую"],
        ["Сегодня", "Статистика"],
        ["Цель"],
    ],
    resize_keyboard=True,
)

GOAL_OPTIONS = {
    "lose": "Похудеть",
    "maintain": "Поддерживать вес",
    "gain": "Набрать массу",
    "feel": "Понять связь еды и самочувствия",
    "custom": "Своя цель",
}

SEX_OPTIONS = {
    "female": "Женщина",
    "male": "Мужчина",
    "other": "Другое",
}

ACTIVITY_OPTIONS = {
    "low": "Низкая",
    "medium": "Средняя",
    "high": "Высокая",
}

PORTION_OPTIONS = {
    "small": ("Маленькая порция", "small"),
    "normal": ("Обычная порция", "normal"),
    "large": ("Большая порция", "large"),
}

WELLBEING_FIELDS = [
    ("satiety", "Насколько ты сейчас сыта?"),
    ("energy", "Сколько у тебя сейчас энергии?"),
    ("heaviness", "Есть ли тяжесть после еды?"),
    ("bloating", "Есть ли вздутие или дискомфорт?"),
    ("mood", "Какое сейчас настроение?"),
]


def build_goal_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for code, label in GOAL_OPTIONS.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"onboard:goal:{code}")])
    return InlineKeyboardMarkup(rows)


def build_sex_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"onboard:sex:{code}")] for code, label in SEX_OPTIONS.items()]
    )


def build_activity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"onboard:activity:{code}")] for code, label in ACTIVITY_OPTIONS.items()]
    )


def build_yes_no_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data=f"{prefix}:1"),
                InlineKeyboardButton("Нет", callback_data=f"{prefix}:0"),
            ]
        ]
    )


def build_portion_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"portion:{code}")] for code, (label, _) in PORTION_OPTIONS.items()]
    )


def build_rating_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(str(value), callback_data=f"wellbeing_rate:{field}:{value}") for value in range(1, 6)]]
    )


def build_skip_note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить заметку", callback_data="wellbeing_note_skip")]])


def build_update_profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Обновить профиль", callback_data="profile:update")]])


def build_followup_keyboard(meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Отметить самочувствие", callback_data=f"wellbeing_start:{meal_id}")]]
    )


def infer_meal_type(now: datetime | None = None) -> str:
    current = now or datetime.now()
    hour = current.hour
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 22:
        return "dinner"
    return "snack"


def format_profile(profile: Any) -> str:
    fat_mode = "включен" if int(profile["fat_insights_enabled"]) else "выключен"
    return (
        "Твой профиль:\n"
        f"Имя: {profile['display_name']}\n"
        f"Цель: {profile['primary_goal']}\n"
        f"Рост/вес: {profile['height_cm']} см / {profile['weight_kg']} кг\n"
        f"Активность: {profile['activity_level']}\n"
        f"Что тревожит: {profile['concerns_text']}\n"
        f"Фокус на жирах: {fat_mode}"
    )


def format_meal_preview(analysis: MealAnalysis) -> str:
    totals = analysis.totals()
    lines = [analysis.source_summary, ""]
    for item in analysis.items:
        lines.append(
            f"• {item.item_name}: ~{item.estimated_grams:g} г | {item.calories:g} ккал"
        )
    lines.extend(
        [
            "",
            f"Итого: {totals['calories']:g} ккал | Б {totals['protein']:g} / Ж {totals['fat']:g} / У {totals['carbs']:g}",
            analysis.quick_note,
            "",
            "Это маленькая, обычная или большая порция?",
            "Если не уверена, нажми «Обычная порция».",
            "Если нужно, можешь еще дописать ингредиенты до выбора порции.",
        ]
    )
    return "\n".join(lines)


def format_meal_saved(analysis: MealAnalysis, fat_insights_enabled: bool, ai: AIService) -> str:
    totals = analysis.totals()
    note = ai.build_nutrition_note(analysis.items, fat_insights_enabled=fat_insights_enabled)
    return (
        "Сохранила прием пищи.\n"
        f"Итого: {totals['calories']:g} ккал | Б {totals['protein']:g} / Ж {totals['fat']:g} / У {totals['carbs']:g}\n"
        f"{note}"
    )


def format_today_summary(db: Database, chat_id: int, ai: AIService) -> str:
    day = today_local_str()
    totals = db.get_today_totals(chat_id, day)
    meals = db.list_meals_for_day(chat_id, day)
    lines = [
        f"Сегодня записано приемов пищи: {totals['meals_count']}",
        f"Калории: {totals['calories']:.0f}",
        f"Белки: {totals['protein']:.1f} г",
        f"Жиры: {totals['fat']:.1f} г",
        f"Углеводы: {totals['carbs']:.1f} г",
        f"Клетчатка: {totals['fiber']:.1f} г",
    ]
    if meals:
        lines.append("")
        lines.append("Последние записи:")
        for meal in meals[-4:]:
            title = meal["source_text"] or meal["meal_type"]
            lines.append(
                f"• {title}: {meal['total_calories']:.0f} ккал | Б {meal['total_protein']:.0f} / Ж {meal['total_fat']:.0f} / У {meal['total_carbs']:.0f}"
            )
    latest_wellbeing = db.latest_wellbeing(chat_id)
    if latest_wellbeing:
        lines.append("")
        lines.append(
            f"Последнее самочувствие: энергия {latest_wellbeing['energy']}/5, тяжесть {latest_wellbeing['heaviness']}/5, настроение {latest_wellbeing['mood']}/5"
        )
    if totals["meals_count"]:
        lines.append("")
        if totals["protein"] >= 70:
            lines.append("По белку день уже выглядит уверенно.")
        if totals["fiber"] < 15:
            lines.append("По клетчатке, похоже, можно добрать овощами, ягодами или бобовыми.")
    else:
        lines.append("")
        lines.append("Пока пусто. Можно написать, что ты съела, или отправить фото/голосовое.")
    return "\n".join(lines)


def format_stats_summary(stats: dict[str, object]) -> str:
    active_days = int(stats["active_days"])
    calories = float(stats["calories"])
    avg_calories = calories / active_days if active_days else 0.0
    lines = [
        f"Статистика за период с {stats['day_from']}:",
        f"Приемов пищи: {stats['meals_count']}",
        f"Активных дней: {active_days}",
        f"Средние калории в активный день: {avg_calories:.0f}",
        f"Белки за период: {float(stats['protein']):.0f} г",
        f"Жиры за период: {float(stats['fat']):.0f} г",
        f"Углеводы за период: {float(stats['carbs']):.0f} г",
    ]
    heavy_items = stats["top_heavy_items"]
    low_energy_items = stats["top_low_energy_items"]
    if heavy_items:
        lines.append("Чаще рядом с тяжестью: " + ", ".join(heavy_items))
    if low_energy_items:
        lines.append("Чаще рядом с низкой энергией: " + ", ".join(low_energy_items))
    if not heavy_items and not low_energy_items:
        lines.append("Пока мало данных для устойчивых паттернов по самочувствию.")
    return "\n".join(lines)


def looks_like_meal_text(text: str) -> bool:
    lowered = text.lower()
    keywords = [
        "съел", "съела", "ела", "ел", "завтрак", "обед", "ужин", "перекус", "поела", "поел",
        "омлет", "рис", "курица", "салат", "йогурт", "латте", "тост", "банан", "авокадо",
    ]
    return any(word in lowered for word in keywords)


def build_meal_context_payload(db: Database, chat_id: int) -> dict[str, str]:
    profile = db.get_profile(chat_id)
    return {
        "primary_goal": profile["primary_goal"] if profile else "",
        "concerns_text": profile["concerns_text"] if profile else "",
    }


async def refresh_meal_draft_with_text(update: Update, context: ContextTypes.DEFAULT_TYPE, extra_text: str) -> bool:
    message = update.message
    if not message:
        return False

    chat_id = update.effective_chat.id
    drafts = context.application.bot_data.setdefault("meal_drafts", {})
    draft = drafts.get(chat_id)
    if not draft:
        return False

    db: Database = context.application.bot_data["db"]
    ai: AIService = context.application.bot_data["ai"]
    source_text = str(draft.get("source_text") or "").strip()
    merged_text = extra_text.strip() if not source_text else f"{source_text}\nДополнение: {extra_text.strip()}"
    context_payload = build_meal_context_payload(db, chat_id)

    source_type = str(draft.get("source_type") or "text")
    image_path = draft.get("image_path")
    if source_type == "photo" and image_path:
        analysis = await ai.analyze_meal_photo(Path(str(image_path)), caption=merged_text, context=context_payload)
        audit_type = "meal_photo_update"
    else:
        analysis = await ai.analyze_meal_text(merged_text, context=context_payload)
        audit_type = "meal_text_update"

    db.log_ai_audit(chat_id, audit_type, merged_text, analysis.raw_json)
    drafts[chat_id] = {
        "analysis": analysis,
        "source_type": source_type,
        "source_text": merged_text,
        "image_path": image_path,
    }
    await message.reply_text(
        "Учла дополнение и пересчитала.\n\n" + format_meal_preview(analysis),
        reply_markup=build_portion_keyboard(),
    )
    return True


async def ensure_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    db: Database = context.application.bot_data["db"]
    if db.get_profile(update.effective_chat.id):
        return True
    sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
    session = sessions.get(update.effective_chat.id)
    if session:
        await remind_onboarding_step(update, context, session)
        return False
    await start_onboarding(update, context, is_update=False)
    return False


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, is_update: bool) -> None:
    chat_id = update.effective_chat.id
    sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
    sessions[chat_id] = {"step": "goal", "is_update": is_update}
    text = "Давай быстро настроим профиль. С какой целью ты сейчас хочешь использовать бота?"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=build_goal_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=build_goal_keyboard())


async def remind_onboarding_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: dict[str, Any],
) -> None:
    step = session.get("step", "goal")
    text = "Давай закончим короткую настройку профиля."
    reply_markup = None

    if step == "goal":
        text = "Сначала закончим настройку профиля. С какой целью ты сейчас хочешь использовать бота?"
        reply_markup = build_goal_keyboard()
    elif step == "display_name":
        text = "Сначала закончим настройку профиля. Как тебя называть в боте?"
    elif step == "sex":
        text = "Сначала закончим настройку профиля. Выбери пол или то, как тебе удобнее обозначить этот параметр."
        reply_markup = build_sex_keyboard()
    elif step == "age":
        text = "Сначала закончим настройку профиля. Сколько тебе лет?"
    elif step == "height_cm":
        text = "Сначала закончим настройку профиля. Какой у тебя рост в сантиметрах?"
    elif step == "weight_kg":
        text = "Сначала закончим настройку профиля. Какой у тебя вес в килограммах?"
    elif step == "activity_level":
        text = "Сначала закончим настройку профиля. Какой у тебя уровень активности?"
        reply_markup = build_activity_keyboard()
    elif step == "concerns":
        text = "Сначала закончим настройку профиля. Что сейчас сильнее всего тревожит или зачем ты ведешь этот дневник?"
    elif step == "fat_insights_enabled":
        text = "Сначала закончим настройку профиля. Включить дополнительный блок с комментариями по качеству жиров?"
        reply_markup = build_yes_no_keyboard("fat_mode")

    message = update.message
    if message:
        await message.reply_text(text, reply_markup=reply_markup)
        return

    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state(context.application.bot_data["settings"].state_path)
    state["primary_chat_id"] = update.effective_chat.id
    save_state(context.application.bot_data["settings"].state_path, state)

    db: Database = context.application.bot_data["db"]
    user = update.effective_user
    db.upsert_user(update.effective_chat.id, user.username if user else None, user.first_name if user else None)
    profile = db.get_profile(update.effective_chat.id)
    if not profile:
        sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
        session = sessions.get(update.effective_chat.id)
        if session:
            await remind_onboarding_step(update, context, session)
        else:
            await start_onboarding(update, context, is_update=False)
        return

    await update.message.reply_text(
        "Я на связи. Можно отправить текст про еду, фото тарелки, голосовое или зайти через кнопки.",
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start — запуск и онбординг\n"
        "/meal — добавить еду текстом\n"
        "/meal — можно также прислать голосовое или фото\n"
        "/feeling — отметить самочувствие\n"
        "/today — сводка за сегодня\n"
        "/stats — базовая статистика\n"
        "/goal — показать и обновить профиль",
        reply_markup=MAIN_MENU,
    )


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return
    db: Database = context.application.bot_data["db"]
    profile = db.get_profile(update.effective_chat.id)
    assert profile is not None
    await update.message.reply_text(
        format_profile(profile),
        reply_markup=build_update_profile_keyboard(),
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return
    db: Database = context.application.bot_data["db"]
    ai: AIService = context.application.bot_data["ai"]
    await update.message.reply_text(format_today_summary(db, update.effective_chat.id, ai), reply_markup=MAIN_MENU)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return
    db: Database = context.application.bot_data["db"]
    stats = db.get_stats_snapshot(update.effective_chat.id)
    await update.message.reply_text(format_stats_summary(stats), reply_markup=MAIN_MENU)


async def meal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return
    payload = " ".join(context.args).strip()
    if payload:
        await analyze_meal_text(update, context, payload)
        return
    sessions = context.application.bot_data.setdefault("meal_entry_sessions", {})
    sessions[update.effective_chat.id] = {"awaiting_text": True}
    await update.message.reply_text(
        "Напиши, что ты съела, или отправь фото/голосовое. Если хочешь, добавь подпись к фото.",
        reply_markup=MAIN_MENU,
    )


async def feeling_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return
    await begin_wellbeing_flow(context, update.effective_chat.id, meal_id=None)


async def analyze_meal_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    db: Database = context.application.bot_data["db"]
    ai: AIService = context.application.bot_data["ai"]
    context_payload = build_meal_context_payload(db, update.effective_chat.id)
    analysis = await ai.analyze_meal_text(text, context=context_payload)
    db.log_ai_audit(update.effective_chat.id, "meal_text", text, analysis.raw_json)
    drafts = context.application.bot_data.setdefault("meal_drafts", {})
    drafts[update.effective_chat.id] = {
        "analysis": analysis,
        "source_type": "text",
        "source_text": text,
        "image_path": None,
    }
    await update.message.reply_text(
        format_meal_preview(analysis),
        reply_markup=build_portion_keyboard(),
    )


async def analyze_meal_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return

    message = update.message
    if not message or not message.photo:
        return

    db: Database = context.application.bot_data["db"]
    ai: AIService = context.application.bot_data["ai"]
    settings: Settings = context.application.bot_data["settings"]
    context_payload = build_meal_context_payload(db, update.effective_chat.id)

    photo = message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = settings.uploads_dir / f"{update.effective_chat.id}_{timestamp}.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    await telegram_file.download_to_drive(custom_path=str(photo_path))

    analysis = await ai.analyze_meal_photo(photo_path, caption=message.caption, context=context_payload)
    db.log_ai_audit(update.effective_chat.id, "meal_photo", message.caption, analysis.raw_json)
    if not analysis.items:
        await message.reply_text(analysis.quick_note, reply_markup=MAIN_MENU)
        return

    drafts = context.application.bot_data.setdefault("meal_drafts", {})
    drafts[update.effective_chat.id] = {
        "analysis": analysis,
        "source_type": "photo",
        "source_text": message.caption or "",
        "image_path": str(photo_path),
    }
    await message.reply_text(format_meal_preview(analysis), reply_markup=build_portion_keyboard())


def convert_audio_to_wav(source_path: Path, target_path: Path) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-i",
            str(source_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(target_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def detect_audio_suffix(audio: Any, telegram_path: str | None) -> str:
    file_name = getattr(audio, "file_name", "") or ""
    suffix = Path(file_name).suffix.lower()
    if suffix:
        return suffix
    if telegram_path:
        detected = Path(telegram_path).suffix.lower()
        if detected:
            return detected
    mime_type = getattr(audio, "mime_type", "") or ""
    if "mpeg" in mime_type or "mp3" in mime_type:
        return ".mp3"
    if "mp4" in mime_type or "m4a" in mime_type:
        return ".m4a"
    if "wav" in mime_type:
        return ".wav"
    if "webm" in mime_type:
        return ".webm"
    return ".ogg"


async def analyze_meal_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_profile(update, context):
        return

    message = update.message
    if not message:
        return

    audio = message.voice or message.audio
    if not audio:
        return

    db: Database = context.application.bot_data["db"]
    ai: AIService = context.application.bot_data["ai"]
    settings: Settings = context.application.bot_data["settings"]
    context_payload = build_meal_context_payload(db, update.effective_chat.id)

    telegram_file = await context.bot.get_file(audio.file_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    source_suffix = detect_audio_suffix(audio, getattr(telegram_file, "file_path", None))
    source_path = settings.uploads_dir / f"{update.effective_chat.id}_{timestamp}{source_suffix}"
    wav_path = settings.uploads_dir / f"{update.effective_chat.id}_{timestamp}.wav"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    await telegram_file.download_to_drive(custom_path=str(source_path))

    try:
        convert_audio_to_wav(source_path, wav_path)
    except Exception:
        await message.reply_text(
            "Не смогла подготовить голосовое к распознаванию. Попробуй еще раз или дублируй текстом.",
            reply_markup=MAIN_MENU,
        )
        return

    try:
        transcript = await ai.transcribe_audio(wav_path)
    finally:
        source_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)

    if not transcript:
        await message.reply_text(
            "Не смогла распознать голосовое. Попробуй сказать короче или продублируй текстом.",
            reply_markup=MAIN_MENU,
        )
        return

    analysis = await ai.analyze_meal_text(transcript, context=context_payload)
    db.log_ai_audit(update.effective_chat.id, "meal_voice", transcript, analysis.raw_json)
    drafts = context.application.bot_data.setdefault("meal_drafts", {})
    drafts[update.effective_chat.id] = {
        "analysis": analysis,
        "source_type": "voice",
        "source_text": transcript,
        "image_path": None,
    }
    await message.reply_text(
        f"Услышала так:\n{transcript}\n\n{format_meal_preview(analysis)}",
        reply_markup=build_portion_keyboard(),
    )


async def onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
    session = sessions.setdefault(chat_id, {})
    _, step, value = (query.data or "").split(":", 2)

    if step == "goal":
        session["primary_goal"] = GOAL_OPTIONS.get(value, value)
        session["step"] = "display_name"
        await query.edit_message_text("Как тебя называть в боте?")
        return

    if step == "sex":
        session["sex"] = SEX_OPTIONS.get(value, value)
        session["step"] = "age"
        await query.edit_message_text("Сколько тебе лет?")
        return

    if step == "activity":
        session["activity_level"] = ACTIVITY_OPTIONS.get(value, value)
        session["step"] = "concerns"
        await query.edit_message_text(
            "Что сейчас сильнее всего тревожит или зачем ты ведешь этот дневник? Можно написать свободно."
        )
        return


async def fat_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
    session = sessions.setdefault(chat_id, {})
    enabled = (query.data or "").endswith(":1")
    session["fat_insights_enabled"] = enabled

    profile = ProfileInput(
        display_name=str(session["display_name"]),
        sex=str(session["sex"]),
        age=int(session["age"]),
        height_cm=float(session["height_cm"]),
        weight_kg=float(session["weight_kg"]),
        activity_level=str(session["activity_level"]),
        primary_goal=str(session["primary_goal"]),
        concerns_text=str(session["concerns_text"]),
        fat_insights_enabled=enabled,
    )
    db: Database = context.application.bot_data["db"]
    db.save_profile(chat_id, profile)
    sessions.pop(chat_id, None)
    await query.edit_message_text("Профиль сохранила. Теперь можно писать про еду текстом или отправлять фото.")
    await context.bot.send_message(chat_id=chat_id, text=format_profile(db.get_profile(chat_id)), reply_markup=MAIN_MENU)


async def update_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    fake_update = Update(update.update_id, callback_query=query)
    await start_onboarding(fake_update, context, is_update=True)


async def portion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    settings: Settings = context.application.bot_data["settings"]
    drafts = context.application.bot_data.setdefault("meal_drafts", {})
    draft = drafts.get(chat_id)
    if not draft:
        await query.edit_message_text("Черновик еды уже потерялся. Давай просто отправь прием пищи еще раз.")
        return

    analysis: MealAnalysis = draft["analysis"]
    portion_code = (query.data or "").split(":", 1)[1]
    scaled = analysis
    if portion_code == "small":
        scaled = analysis.scaled(settings.default_portion_small_factor, "Я уменьшила расчет под маленькую порцию.")
    elif portion_code == "large":
        scaled = analysis.scaled(settings.default_portion_large_factor, "Я увеличила расчет под большую порцию.")

    db: Database = context.application.bot_data["db"]
    profile = db.get_profile(chat_id)
    meal_id = db.create_meal(
        telegram_chat_id=chat_id,
        day=today_local_str(),
        source_type=str(draft["source_type"]),
        source_text=str(draft["source_text"] or ""),
        image_path=draft["image_path"],
        meal_type=infer_meal_type(),
        portion_label=portion_code,
        confidence=scaled.confidence,
        items=[item.as_db_dict() for item in scaled.items],
    )
    drafts.pop(chat_id, None)

    ai: AIService = context.application.bot_data["ai"]
    fat_mode = bool(profile["fat_insights_enabled"]) if profile else False
    await query.edit_message_text(format_meal_saved(scaled, fat_mode, ai))

    minutes = settings.wellbeing_followup_minutes
    context.job_queue.run_once(
        wellbeing_followup_job,
        when=minutes * 60,
        data={"chat_id": chat_id, "meal_id": meal_id},
        name=f"wellbeing-followup-{meal_id}",
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Если хочешь, через {minutes} минут я еще напомню отметить самочувствие после еды.",
        reply_markup=MAIN_MENU,
    )


async def begin_wellbeing_flow(context: ContextTypes.DEFAULT_TYPE, chat_id: int, meal_id: int | None) -> None:
    sessions = context.application.bot_data.setdefault("wellbeing_sessions", {})
    sessions[chat_id] = {"meal_id": meal_id, "answers": {}, "step_index": 0}
    field, prompt = WELLBEING_FIELDS[0]
    await context.bot.send_message(chat_id=chat_id, text=prompt, reply_markup=build_rating_keyboard(field))


async def wellbeing_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    meal_id = int((query.data or "").split(":", 1)[1])
    await query.edit_message_text("Ок, давай быстро отметим самочувствие после этого приема пищи.")
    await begin_wellbeing_flow(context, query.message.chat_id, meal_id=meal_id)


async def wellbeing_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    sessions = context.application.bot_data.setdefault("wellbeing_sessions", {})
    session = sessions.get(chat_id)
    if not session:
        await query.edit_message_text("Сессия самочувствия уже завершена. Можем начать заново.")
        return

    _, field, value = (query.data or "").split(":")
    session["answers"][field] = int(value)
    session["step_index"] += 1

    if session["step_index"] >= len(WELLBEING_FIELDS):
        session["awaiting_note"] = True
        await query.edit_message_text(
            "Если хочешь, добавь короткую заметку одним сообщением. Или нажми кнопку ниже.",
            reply_markup=build_skip_note_keyboard(),
        )
        return

    next_field, next_prompt = WELLBEING_FIELDS[session["step_index"]]
    await query.edit_message_text(next_prompt, reply_markup=build_rating_keyboard(next_field))


async def wellbeing_skip_note_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await finish_wellbeing_flow(context, query.message.chat_id, note=None)
    await query.edit_message_text("Самочувствие сохранила.")


async def finish_wellbeing_flow(context: ContextTypes.DEFAULT_TYPE, chat_id: int, note: str | None) -> None:
    sessions = context.application.bot_data.setdefault("wellbeing_sessions", {})
    session = sessions.pop(chat_id, None)
    if not session:
        return
    answers = session["answers"]
    db: Database = context.application.bot_data["db"]
    db.log_wellbeing(
        telegram_chat_id=chat_id,
        meal_id=session.get("meal_id"),
        satiety=int(answers.get("satiety", 3)),
        energy=int(answers.get("energy", 3)),
        heaviness=int(answers.get("heaviness", 3)),
        bloating=int(answers.get("bloating", 3)),
        mood=int(answers.get("mood", 3)),
        note=note,
    )
    await context.bot.send_message(chat_id=chat_id, text="Отметила самочувствие. Это пойдет в паттерны и статистику.", reply_markup=MAIN_MENU)


async def wellbeing_followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    meal_id = data.get("meal_id")
    if not chat_id or not meal_id:
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="Как ты чувствуешь себя после этой еды?",
        reply_markup=build_followup_keyboard(int(meal_id)),
    )


async def free_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    chat_id = update.effective_chat.id

    if text == "Добавить еду":
        await meal_command(update, context)
        return
    if text == "Как я себя чувствую":
        await feeling_command(update, context)
        return
    if text == "Сегодня":
        await today_command(update, context)
        return
    if text == "Статистика":
        await stats_command(update, context)
        return
    if text == "Цель":
        await goal_command(update, context)
        return

    onboarding_sessions = context.application.bot_data.setdefault("onboarding_sessions", {})
    onboarding = onboarding_sessions.get(chat_id)
    if onboarding:
        await handle_onboarding_text(update, context, onboarding)
        return

    wellbeing_sessions = context.application.bot_data.setdefault("wellbeing_sessions", {})
    wellbeing = wellbeing_sessions.get(chat_id)
    if wellbeing and wellbeing.get("awaiting_note"):
        note = None if text.lower() in {"пропустить", "skip"} else text
        await finish_wellbeing_flow(context, chat_id, note=note)
        return

    meal_sessions = context.application.bot_data.setdefault("meal_entry_sessions", {})
    if meal_sessions.pop(chat_id, None) is not None:
        await analyze_meal_text(update, context, text)
        return

    if await refresh_meal_draft_with_text(update, context, text):
        return

    if looks_like_meal_text(text):
        await analyze_meal_text(update, context, text)
        return

    await message.reply_text(
        "Я пока лучше всего понимаю еду, самочувствие и вопросы по статистике. Можешь написать, что ты съела, или нажать кнопку.",
        reply_markup=MAIN_MENU,
    )


async def handle_onboarding_text(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict[str, Any]) -> None:
    text = update.message.text.strip()
    step = session.get("step")
    if step == "display_name":
        session["display_name"] = text
        session["step"] = "sex"
        await update.message.reply_text("Выбери пол или то, как тебе удобнее обозначить этот параметр.", reply_markup=build_sex_keyboard())
        return
    if step == "age":
        if not text.isdigit():
            await update.message.reply_text("Возраст лучше прислать числом.")
            return
        session["age"] = int(text)
        session["step"] = "height_cm"
        await update.message.reply_text("Какой у тебя рост в сантиметрах?")
        return
    if step == "height_cm":
        try:
            session["height_cm"] = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Рост лучше прислать числом, например 170.")
            return
        session["step"] = "weight_kg"
        await update.message.reply_text("Какой у тебя вес в килограммах?")
        return
    if step == "weight_kg":
        try:
            session["weight_kg"] = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Вес лучше прислать числом, например 63.5.")
            return
        session["step"] = "activity_level"
        await update.message.reply_text("Какой у тебя уровень активности?", reply_markup=build_activity_keyboard())
        return
    if step == "concerns":
        session["concerns_text"] = text
        session["step"] = "fat_insights_enabled"
        await update.message.reply_text(
            "Хочешь включить дополнительный блок с комментариями по качеству жиров? Это опционально.",
            reply_markup=build_yes_no_keyboard("fat_mode"),
        )
        return


def build_application(settings: Settings, db: Database, ai: AIService) -> Application:
    request = HTTPXRequest(http_version="1.1", httpx_kwargs={"trust_env": False})
    application = Application.builder().token(settings.telegram_bot_token).request(request).get_updates_request(request).build()
    application.bot_data["settings"] = settings
    application.bot_data["db"] = db
    application.bot_data["ai"] = ai
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("meal", meal_command))
    application.add_handler(CommandHandler("feeling", feeling_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("goal", goal_command))
    application.add_handler(CallbackQueryHandler(onboarding_callback, pattern=r"^onboard:"))
    application.add_handler(CallbackQueryHandler(fat_mode_callback, pattern=r"^fat_mode:"))
    application.add_handler(CallbackQueryHandler(update_profile_callback, pattern=r"^profile:update$"))
    application.add_handler(CallbackQueryHandler(portion_callback, pattern=r"^portion:"))
    application.add_handler(CallbackQueryHandler(wellbeing_start_callback, pattern=r"^wellbeing_start:"))
    application.add_handler(CallbackQueryHandler(wellbeing_rating_callback, pattern=r"^wellbeing_rate:"))
    application.add_handler(CallbackQueryHandler(wellbeing_skip_note_callback, pattern=r"^wellbeing_note_skip$"))
    application.add_handler(MessageHandler(filters.PHOTO, analyze_meal_photo))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, analyze_meal_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_message))
    return application
