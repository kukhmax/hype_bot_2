"""
Gemini API клиент.
Формирует развернутый описательный отчет по торговому сетапу, 
который затем передается в DeepSeek.
"""
import logging
import aiohttp

from config import config
from core.indicators import SetupResult

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """Ты — независимый аналитик крипторынка. Твоя задача — объективно проанализировать торговый сетап (направление, объемы, уровни) и написать краткий, но аргументированный вывод. 
Ты не принимаешь окончательного решения о входе, ты только даешь экспертную оценку происходящего на графике (пробой, ловушка, перекупленность и т.д.).
Формат ответа: 2-3 абзаца чистого текста без разметки markdown, только суть."""

def build_gemini_prompt(
    token: str,
    tf: str,
    setup: SetupResult,
    current_price: float,
) -> str:
    direction_ru = "ЛОНГ" if setup.direction == "LONG" else "ШОРТ"
    
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

Напиши короткий аргументированный отчет по этой ситуации. Опиши, насколько надежно выглядит этот пробой, есть ли признаки ловушки крупного игрока, и стоит ли отрабатывать этот сигнал.
"""

async def analyze_setup_with_gemini(
    token: str,
    tf: str,
    setup: SetupResult,
    current_price: float,
) -> str | None:
    """Запрашивает аналитический отчет у Gemini 2.5."""
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
            "temperature": 0.5,
            "maxOutputTokens": 600,
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
        return content.strip()

    except Exception as e:
        logger.error(f"Gemini request failed: {e}")
        return None
