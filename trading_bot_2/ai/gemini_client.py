"""
Клиент Gemini AI для подтверждения торгового сигнала.
Получает контекст (паттерны, индикаторы, цены) → возвращает анализ + % уверенности.
"""
import json
import re
import asyncio
from typing import Optional
import google.generativeai as genai

from config import config
from utils.logger import logger


SYSTEM_PROMPT = """Ты — профессиональный трейдер и аналитик финансовых рынков.
Тебе дают технический анализ: найденные свечные паттерны, значения индикаторов и предлагаемую сделку.
Твоя задача: оценить качество сетапа и дать % уверенности от 0 до 100.

Отвечай СТРОГО в формате JSON:
{
  "confidence": <число 0-100>,
  "analysis": "<краткий анализ на русском, 2-3 предложения>",
  "risks": "<главные риски, 1 предложение>",
  "verdict": "ENTER" | "SKIP" | "WAIT"
}

Критерии:
- confidence 80-100: отличный сетап, все факторы совпадают
- confidence 60-79: хороший сетап, незначительные риски  
- confidence 40-59: слабый сетап, высокие риски
- confidence 0-39: плохой сетап, пропустить
"""


class GeminiClient:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY не задан в .env")
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT,
        )

    async def analyze_signal(
        self,
        symbol: str,
        timeframe: str,
        direction: str,
        patterns: list,
        indicators,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> dict:
        """
        Асинхронный запрос к Gemini.
        Возвращает dict: {confidence, analysis, risks, verdict}
        """
        prompt = self._build_prompt(
            symbol, timeframe, direction, patterns, indicators, entry, stop_loss, take_profit
        )
        logger.debug(f"[Gemini] ({symbol} {timeframe}) Отправка запроса к AI. Размер промпта: {len(prompt)} символов.")

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self.model.generate_content(prompt)
            )
            raw = response.text.strip()
            logger.debug(f"[Gemini] ({symbol} {timeframe}) Успешный ответ от AI. Длина: {len(raw)} символов.\nСырой ответ:\n{raw}")
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"[Gemini] Ошибка API: {e}")
            return {"confidence": 50, "analysis": f"Ошибка AI: {e}", "risks": "", "verdict": "WAIT"}

    def _build_prompt(
        self, symbol, timeframe, direction, patterns, indicators, entry, stop_loss, take_profit
    ) -> str:
        pattern_list = "\n".join(
            f"  - {p.name}: сила={p.strength:.2f}, {p.description}"
            for p in patterns
        )
        atr_pct = indicators.atr_pct

        return f"""
## Торговый сетап для анализа

**Инструмент:** {symbol} ({timeframe})
**Направление:** {direction}
**ATR%:** {atr_pct:.2f}% (волатильность)

### Активные паттерны ({len(patterns)} шт.):
{pattern_list}

### Индикаторы:
- EMA10={indicators.ema10:.4f}, EMA20={indicators.ema20:.4f}, EMA50={indicators.ema50:.4f}
- ADX={indicators.adx:.1f} (+DI={indicators.plus_di:.1f}, -DI={indicators.minus_di:.1f})
- RSI={indicators.rsi:.1f}
- CCI={indicators.cci:.0f}
- Тренд EMA: {'восходящий ✅' if indicators.trend_up else 'нисходящий ✅' if indicators.trend_down else 'боковик ⚠️'}

### Параметры сделки:
- Вход: {entry:.6f}
- Стоп-лосс: {stop_loss:.6f} (риск: {abs(entry-stop_loss)/entry*100:.2f}%)
- Тейк-профит: {take_profit:.6f} (цель: {abs(take_profit-entry)/entry*100:.2f}%)
- R/R: {abs(take_profit-entry)/abs(entry-stop_loss):.2f}

Оцени качество сетапа и дай % уверенности. Учитывай согласованность паттернов, 
силу тренда, соотношение R/R и текущую волатильность.
"""

    def _parse_response(self, raw: str) -> dict:
        # Извлечь JSON из ответа
        try:
            # Попробуем найти JSON блок
            match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    "confidence": int(data.get("confidence", 50)),
                    "analysis": str(data.get("analysis", "")),
                    "risks": str(data.get("risks", "")),
                    "verdict": str(data.get("verdict", "WAIT")),
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # Фолбэк: ищем число уверенности в тексте
        match = re.search(r'(\d{1,3})\s*%', raw)
        confidence = int(match.group(1)) if match else 50
        return {
            "confidence": min(100, confidence),
            "analysis": raw[:300],
            "risks": "",
            "verdict": "WAIT",
        }
