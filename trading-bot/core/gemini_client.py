"""
Gemini API клиент.
Анализирует параметры сетапа и возвращает структурированный JSON ответ.
"""
import json
import logging
import aiohttp

from config import config
from core.indicators import SetupResult

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """Ты — независимый аналитик крипторынка. Твоя задача — объективно проанализировать торговый сетап (направление, объемы, уровни) и выдать выводы.
Ты должен ответить строго в формате JSON, соответствующем заданному формату ответа."""

def build_gemini_prompt(
    token: str,
    tf: str,
    setup: SetupResult,
    current_price: float,
) -> str:
    direction_ru = "ЛОНГ" if setup.direction == "LONG" else "ШОРТ"
    
    sl_hint = (
        f"Ориентировочный стоп — ниже EMA20_Low ({setup.ema_low_last:.4f}) "
        f"или ниже PH ({setup.prev_high:.4f})"
        if setup.direction == "LONG"
        else
        f"Ориентировочный стоп — выше EMA20_High ({setup.ema_high_last:.4f}) "
        f"или выше PL ({setup.prev_low:.4f})"
    )
    
    return f"""
Проведи анализ следующего торгового сетапа.

Инструмент: {token} PERP
Таймфрейм: {tf}
Текущая цена: {current_price:.4f}
Сигнал стратегии: {direction_ru}

Технические данные:
- EMA20(High): {setup.ema_high_last:.4f}
- EMA20(Low):  {setup.ema_low_last:.4f}
- ADX:         {setup.adx_value:.2f} (сила тренда)
- +DI:         {setup.plus_di:.2f}
- -DI:         {setup.minus_di:.2f}
- Пробойный объем: {setup.volume_ratio:.2f}x от среднего (важно: >1.3 считается повышенным)

Предыдущие экстремумы:
- Максимум (PH): {setup.prev_high:.4f}
- Минимум (PL): {setup.prev_low:.4f}

ЗАДАЧА:
1. Оцени надежность пробоя, наличие объемов и силы тренда.
2. Рассчитай точки входа, стоп ({sl_hint}) и тейк-профиты (RR >= 1.5 для TP1, RR >= 2.5 для TP2).
3. Дай вердикт: AGGRESSIVE_ENTRY / CAUTIOUS_ENTRY / SKIP.
4. Напиши краткий аргументированный отчет (2-3 предложения).

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "verdict": "AGGRESSIVE_ENTRY" | "CAUTIOUS_ENTRY" | "SKIP",
  "confidence": 0-100,
  "direction": "{setup.direction}",
  "entry_low": <число>,
  "entry_high": <число>,
  "stop_loss": <число>,
  "take_profit_1": <число>,
  "take_profit_2": <число>,
  "rr_ratio_tp1": <число>,
  "rr_ratio_tp2": <число>,
  "analysis": "<краткое описание сетапа на русском, 2-3 предложения>",
  "skip_reason": "<причина если SKIP, иначе null>"
}}
"""

async def analyze_setup_with_gemini(
    token: str,
    tf: str,
    setup: SetupResult,
    current_price: float,
) -> dict | None:
    """Запрашивает аналитический JSON отчет у Gemini 2.5."""
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is missing, skipping Gemini analysis.")
        return None

    prompt = build_gemini_prompt(token, tf, setup, current_price)

    payload = {
        "system_instruction": {
            "parts": {"text": SYSTEM_INSTRUCTION}
        },
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "response_mime_type": "application/json",
        }
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Gemini error {resp.status}: {text}")
                    return None
                data = await resp.json()

        content = data["candidates"][0]["content"]["parts"][0]["text"]
        logger.info(f"Gemini report received for {token}/{tf}: {content[:100]}...")
        
        # Очищаем ответ от возможной markdown разметки json
        if content.startswith("```json"):
            content = content[7:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
            
        return json.loads(content)

    except Exception as e:
        logger.error(f"Gemini request failed: {e}")
        return None
