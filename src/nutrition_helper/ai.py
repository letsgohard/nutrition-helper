from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nutrition_helper.config import Settings

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None


@dataclass
class MealItemEstimate:
    item_name: str
    estimated_amount: str
    amount_unit: str
    estimated_grams: float
    calories: float
    protein: float
    fat: float
    carbs: float
    fiber: float
    saturated_fat: float

    def scaled(self, factor: float) -> "MealItemEstimate":
        return MealItemEstimate(
            item_name=self.item_name,
            estimated_amount=self.estimated_amount,
            amount_unit=self.amount_unit,
            estimated_grams=round(self.estimated_grams * factor, 1),
            calories=round(self.calories * factor, 1),
            protein=round(self.protein * factor, 1),
            fat=round(self.fat * factor, 1),
            carbs=round(self.carbs * factor, 1),
            fiber=round(self.fiber * factor, 1),
            saturated_fat=round(self.saturated_fat * factor, 1),
        )

    def as_db_dict(self) -> dict[str, float | str]:
        return asdict(self)


@dataclass
class MealAnalysis:
    items: list[MealItemEstimate]
    confidence: float
    source_summary: str
    quick_note: str
    raw_json: str

    def scaled(self, factor: float, summary_suffix: str) -> "MealAnalysis":
        return MealAnalysis(
            items=[item.scaled(factor) for item in self.items],
            confidence=self.confidence,
            source_summary=self.source_summary,
            quick_note=f"{self.quick_note} {summary_suffix}".strip(),
            raw_json=self.raw_json,
        )

    def totals(self) -> dict[str, float]:
        return {
            "calories": round(sum(item.calories for item in self.items), 1),
            "protein": round(sum(item.protein for item in self.items), 1),
            "fat": round(sum(item.fat for item in self.items), 1),
            "carbs": round(sum(item.carbs for item in self.items), 1),
            "fiber": round(sum(item.fiber for item in self.items), 1),
            "saturated_fat": round(sum(item.saturated_fat for item in self.items), 1),
        }


FOOD_CATALOG = [
    {
        "aliases": ["яйцо", "яйца", "egg", "eggs"],
        "amount": "1",
        "unit": "piece",
        "grams": 50.0,
        "calories": 78.0,
        "protein": 6.3,
        "fat": 5.3,
        "carbs": 0.6,
        "fiber": 0.0,
        "saturated_fat": 1.6,
    },
    {
        "aliases": ["омлет", "omelet", "omelette"],
        "amount": "1",
        "unit": "portion",
        "grams": 120.0,
        "calories": 180.0,
        "protein": 12.0,
        "fat": 14.0,
        "carbs": 2.0,
        "fiber": 0.0,
        "saturated_fat": 4.0,
    },
    {
        "aliases": ["тост", "toast", "bread", "хлеб"],
        "amount": "1",
        "unit": "slice",
        "grams": 30.0,
        "calories": 80.0,
        "protein": 2.8,
        "fat": 1.0,
        "carbs": 15.0,
        "fiber": 1.2,
        "saturated_fat": 0.2,
    },
    {
        "aliases": ["курица", "chicken", "chicken breast"],
        "amount": "1",
        "unit": "portion",
        "grams": 140.0,
        "calories": 231.0,
        "protein": 43.0,
        "fat": 5.0,
        "carbs": 0.0,
        "fiber": 0.0,
        "saturated_fat": 1.4,
    },
    {
        "aliases": ["рис", "rice"],
        "amount": "1",
        "unit": "portion",
        "grams": 150.0,
        "calories": 195.0,
        "protein": 4.1,
        "fat": 0.4,
        "carbs": 42.0,
        "fiber": 0.6,
        "saturated_fat": 0.1,
    },
    {
        "aliases": ["салат", "овощи", "vegetables", "salad"],
        "amount": "1",
        "unit": "portion",
        "grams": 120.0,
        "calories": 36.0,
        "protein": 1.8,
        "fat": 0.4,
        "carbs": 6.5,
        "fiber": 2.7,
        "saturated_fat": 0.1,
    },
    {
        "aliases": ["авокадо", "avocado"],
        "amount": "1/2",
        "unit": "piece",
        "grams": 75.0,
        "calories": 120.0,
        "protein": 1.5,
        "fat": 11.0,
        "carbs": 6.0,
        "fiber": 5.0,
        "saturated_fat": 1.6,
    },
    {
        "aliases": ["киви", "kiwi"],
        "amount": "1",
        "unit": "piece",
        "grams": 75.0,
        "calories": 46.0,
        "protein": 0.8,
        "fat": 0.4,
        "carbs": 11.0,
        "fiber": 2.2,
        "saturated_fat": 0.0,
    },
    {
        "aliases": ["картошка фри", "fries", "french fries"],
        "amount": "1",
        "unit": "portion",
        "grams": 130.0,
        "calories": 405.0,
        "protein": 4.5,
        "fat": 20.0,
        "carbs": 53.0,
        "fiber": 4.5,
        "saturated_fat": 3.6,
    },
    {
        "aliases": ["латте", "latte"],
        "amount": "1",
        "unit": "cup",
        "grams": 300.0,
        "calories": 160.0,
        "protein": 8.0,
        "fat": 7.0,
        "carbs": 15.0,
        "fiber": 0.0,
        "saturated_fat": 4.0,
    },
    {
        "aliases": ["круассан", "croissant"],
        "amount": "1",
        "unit": "piece",
        "grams": 60.0,
        "calories": 245.0,
        "protein": 4.7,
        "fat": 12.0,
        "carbs": 28.0,
        "fiber": 1.5,
        "saturated_fat": 7.0,
    },
    {
        "aliases": ["йогурт", "yogurt", "greek yogurt"],
        "amount": "1",
        "unit": "cup",
        "grams": 170.0,
        "calories": 120.0,
        "protein": 15.0,
        "fat": 4.0,
        "carbs": 6.0,
        "fiber": 0.0,
        "saturated_fat": 2.5,
    },
    {
        "aliases": ["банан", "banana"],
        "amount": "1",
        "unit": "piece",
        "grams": 120.0,
        "calories": 105.0,
        "protein": 1.3,
        "fat": 0.3,
        "carbs": 27.0,
        "fiber": 3.1,
        "saturated_fat": 0.1,
    },
    {
        "aliases": ["овсянка", "oatmeal", "каша"],
        "amount": "1",
        "unit": "bowl",
        "grams": 220.0,
        "calories": 150.0,
        "protein": 5.0,
        "fat": 3.0,
        "carbs": 27.0,
        "fiber": 4.0,
        "saturated_fat": 0.5,
    },
]


class AIService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = None
        if settings.openai_api_key and AsyncOpenAI is not None:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def analyze_meal_text(self, text: str, context: dict[str, Any] | None = None) -> MealAnalysis:
        if self.client:
            try:
                return await self._analyze_with_openai(text=text, image_path=None, context=context or {})
            except Exception:
                pass
        return self._fallback_text_analysis(text)

    async def analyze_meal_photo(
        self,
        image_path: Path,
        caption: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> MealAnalysis:
        if self.client:
            try:
                return await self._analyze_with_openai(text=caption or "", image_path=image_path, context=context or {})
            except Exception:
                pass
        if caption:
            return self._fallback_text_analysis(caption)
        return MealAnalysis(
            items=[],
            confidence=0.2,
            source_summary="Фото принято, но без AI или подписи я не могу честно оценить состав.",
            quick_note="Лучше добавь короткую подпись к фото: что это и насколько большая порция.",
            raw_json='{"fallback":"photo_without_caption"}',
        )

    async def transcribe_audio(self, audio_path: Path) -> str:
        if self.client:
            try:
                return await self._transcribe_with_openai(audio_path)
            except Exception:
                pass
        return ""

    async def _analyze_with_openai(
        self,
        text: str,
        image_path: Path | None,
        context: dict[str, Any],
    ) -> MealAnalysis:
        profile_goal = context.get("primary_goal") or "support"
        concerns = context.get("concerns_text") or ""
        prompt = f"""
Ты помогаешь Telegram-боту nutrition-helper разбирать приемы пищи.

Верни только JSON без markdown.

Формат:
{{
  "confidence": 0.0,
  "source_summary": "короткое описание того, что ты увидел",
  "quick_note": "очень короткий практический nutrition insight на русском",
  "items": [
    {{
      "item_name": "строка",
      "estimated_amount": "строка вроде 1 порция / 2 яйца / 1 чашка",
      "amount_unit": "piece|portion|cup|bowl|plate|g",
      "estimated_grams": 0,
      "calories": 0,
      "protein": 0,
      "fat": 0,
      "carbs": 0,
      "fiber": 0,
      "saturated_fat": 0
    }}
  ]
}}

Правила:
- Нужна грубая, но честная оценка, а не псевдоточная.
- Если фото не дает точности, оцени умеренно и не выдумывай детали.
- Если есть текст пользователя, используй его как сильный сигнал.
- Держи ответ коротким.
- Цель пользователя: {profile_goal}
- Что тревожит: {concerns}
""".strip()

        content: list[dict[str, Any]] = [{"type": "input_text", "text": f"{prompt}\n\nСообщение пользователя: {text or 'нет текста'}"}]
        if image_path:
            data_url = self._image_path_to_data_url(image_path)
            content.append({"type": "input_image", "image_url": data_url, "detail": "auto"})

        response = await self.client.responses.create(
            model=self.settings.openai_model,
            input=[{"role": "user", "content": content}],
        )
        raw = (response.output_text or "").strip()
        payload = json.loads(raw)
        items = [self._item_from_payload(item) for item in payload.get("items", [])]
        return MealAnalysis(
            items=items,
            confidence=float(payload.get("confidence") or 0.7),
            source_summary=str(payload.get("source_summary") or "Прием пищи распознан."),
            quick_note=str(payload.get("quick_note") or "Нормальный базовый прием пищи."),
            raw_json=raw,
        )

    def _image_path_to_data_url(self, image_path: Path) -> str:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        suffix = image_path.suffix.lower()
        mime_type = "image/jpeg"
        if suffix == ".png":
            mime_type = "image/png"
        return f"data:{mime_type};base64,{encoded}"

    async def _transcribe_with_openai(self, audio_path: Path) -> str:
        with audio_path.open("rb") as audio_file:
            transcription = await self.client.audio.transcriptions.create(
                model=self.settings.openai_transcribe_model,
                file=audio_file,
            )
        text = getattr(transcription, "text", "") or ""
        return str(text).strip()

    def _item_from_payload(self, item: dict[str, Any]) -> MealItemEstimate:
        return MealItemEstimate(
            item_name=str(item.get("item_name") or "Unknown item"),
            estimated_amount=str(item.get("estimated_amount") or "1 portion"),
            amount_unit=str(item.get("amount_unit") or "portion"),
            estimated_grams=float(item.get("estimated_grams") or 0.0),
            calories=float(item.get("calories") or 0.0),
            protein=float(item.get("protein") or 0.0),
            fat=float(item.get("fat") or 0.0),
            carbs=float(item.get("carbs") or 0.0),
            fiber=float(item.get("fiber") or 0.0),
            saturated_fat=float(item.get("saturated_fat") or 0.0),
        )

    def _fallback_text_analysis(self, text: str) -> MealAnalysis:
        lowered = text.lower()
        items: list[MealItemEstimate] = []
        matched_aliases: set[str] = set()
        for entry in FOOD_CATALOG:
            alias = next((alias for alias in entry["aliases"] if alias in lowered), None)
            if not alias or alias in matched_aliases:
                continue
            multiplier = self._extract_multiplier(lowered, entry["aliases"])
            matched_aliases.add(alias)
            items.append(
                MealItemEstimate(
                    item_name=str(entry["aliases"][0]).capitalize(),
                    estimated_amount=f"{multiplier:g} {entry['unit']}",
                    amount_unit=str(entry["unit"]),
                    estimated_grams=round(float(entry["grams"]) * multiplier, 1),
                    calories=round(float(entry["calories"]) * multiplier, 1),
                    protein=round(float(entry["protein"]) * multiplier, 1),
                    fat=round(float(entry["fat"]) * multiplier, 1),
                    carbs=round(float(entry["carbs"]) * multiplier, 1),
                    fiber=round(float(entry["fiber"]) * multiplier, 1),
                    saturated_fat=round(float(entry["saturated_fat"]) * multiplier, 1),
                )
            )

        if not items:
            items.append(
                MealItemEstimate(
                    item_name="Неопознанное блюдо",
                    estimated_amount="1 portion",
                    amount_unit="portion",
                    estimated_grams=250.0,
                    calories=350.0,
                    protein=12.0,
                    fat=14.0,
                    carbs=35.0,
                    fiber=3.0,
                    saturated_fat=4.0,
                )
            )
            summary = "Я не уверена в составе, поэтому дала очень грубую оценку одной средней порции."
            note = "Если хочешь точнее, пиши ключевые продукты через запятую или отправляй фото с подписью."
            confidence = 0.3
        else:
            summary = "Похоже, я собрала основные продукты из сообщения."
            note = self.build_nutrition_note(items, fat_insights_enabled=False)
            confidence = 0.6

        return MealAnalysis(
            items=items,
            confidence=confidence,
            source_summary=summary,
            quick_note=note,
            raw_json=json.dumps({"fallback": text, "items": [item.as_db_dict() for item in items]}, ensure_ascii=False),
        )

    def _extract_multiplier(self, text: str, aliases: list[str]) -> float:
        for alias in aliases:
            pattern = rf"(\d+(?:[.,]\d+)?)\s+{re.escape(alias)}"
            match = re.search(pattern, text)
            if match:
                return max(0.5, float(match.group(1).replace(",", ".")))
        return 1.0

    def build_nutrition_note(self, items: list[MealItemEstimate], fat_insights_enabled: bool) -> str:
        totals = {
            "protein": sum(item.protein for item in items),
            "fat": sum(item.fat for item in items),
            "carbs": sum(item.carbs for item in items),
            "fiber": sum(item.fiber for item in items),
            "saturated_fat": sum(item.saturated_fat for item in items),
        }
        notes: list[str] = []

        if totals["protein"] >= 25:
            notes.append("Хорошо по белку.")
        elif totals["protein"] < 15:
            notes.append("По белку здесь, похоже, скромно.")

        if totals["fiber"] < 4:
            notes.append("Клетчатки маловато, можно добавить овощи, ягоды или бобовые.")
        elif totals["fiber"] >= 8:
            notes.append("Неплохо по клетчатке.")

        if totals["carbs"] >= 45 and totals["protein"] < 20:
            notes.append("Здесь немало углеводов, поэтому сытость может уйти быстрее без добавки белка.")

        if fat_insights_enabled:
            if totals["saturated_fat"] >= 6:
                notes.append("По качеству жиров тут есть риск перегруза насыщенными жирами.")
            elif totals["fat"] >= 10:
                notes.append("По жирам выглядит спокойно, без явного перегруза.")

        if not notes:
            notes.append("Выглядит как нормальная базовая еда без явных перекосов.")
        return " ".join(notes[:3])
