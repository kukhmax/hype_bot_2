"""
DeepSeek API клиент.
Формирует промпт по стратегии и парсит ответ.
"""
import json
import logging
import aiohttp

from config import config
from core.indicators import SetupResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — ветеран криптотрейдинга, специалист по внутридневной торговле \
фьючерсами. Специализация — подтверждение точек входа на основе ценовой структуры, \
объёма и мультитаймфреймного контекста. Отвечай строго в формате JSON."""


def build_analysis_prompt(
    token: str,
    tf: str,
    setup: SetupResult,
    current_price: float,
) -> str:
    direction_ru = "ЛОНГ" if setup.direction == "LONG" else "ШОРТ"
    condition = (
        f"Цена закрытия ({setup.close_last:.4f}) выше EMA20_High ({setup.ema_high_last:.4f}) "
        f"И выше предыдущего максимума PH ({setup.prev_high:.4f}). "
        f"PH находился выше EMA20_High — структурный пробой подтверждён."
        if setup.direction == "LONG"
        else
        f"Цена закрытия ({setup.close_last:.4f}) ниже EMA20_Low ({setup.ema_low_last:.4f}) "
        f"И ниже предыдущего минимума PL ({setup.prev_low:.4f}). "
        f"PL находился ниже EMA20_Low — структурный пробой подтверждён."
    )

    sl_hint = (
        f"Ориентировочный стоп — ниже EMA20_Low ({setup.ema_low_last:.4f}) "
        f"или ниже PH ({setup.prev_high:.4f})"
        if setup.direction == "LONG"
        else
        f"Ориентировочный стоп — выше EMA20_High ({setup.ema_high_last:.4f}) "
        f"или выше PL ({setup.prev_low:.4f})"
    )

    return f"""
Инструмент: {token} PERP (Hyperliquid)
Таймфрейм: {tf}
Текущая цена: {current_price:.4f}
Сигнал стратегии: {direction_ru}

=== ДАННЫЕ ИНДИКАТОРОВ ===
EMA20(High): {setup.ema_high_last:.4f}
EMA20(Low):  {setup.ema_low_last:.4f}
ADX:         {setup.adx_value:.2f} (порог >20)
+DI:         {setup.plus_di:.2f}
-DI:         {setup.minus_di:.2f}
Объём (ratio к среднему 20): {setup.volume_ratio:.2f}x

=== УСЛОВИЯ СЕТАПА ===
{condition}

=== ЗАДАЧА ===
1. Оцени качество сигнала: это сильный пробой или потенциальная ловушка?
2. Учти объём (ratio {setup.volume_ratio:.2f}x — пробой на объёме выше 1.3 считается подтверждённым).
3. Рассчитай:
   - Точку входа (диапазон ±0.1% от текущей цены или ретест уровня)
   - Стоп-лосс: {sl_hint}
   - Take Profit 1 (ближайшее сопротивление/поддержка, RR ≥ 1.5)
   - Take Profit 2 (расширенная цель, RR ≥ 2.5)
4. Вынеси вердикт: AGGRESSIVE_ENTRY / CAUTIOUS_ENTRY / SKIP

=== ФОРМАТ ОТВЕТА (строго JSON) ===
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
""".strip()


async def analyze_setup(
    token: str,
    tf: str,
    setup: SetupResult,
) -> dict | None:
    """Отправить запрос в DeepSeek и вернуть распарсенный JSON или None."""
    prompt = build_analysis_prompt(token, tf, setup, setup.close_last)

    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.DEEPSEEK_BASE_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"DeepSeek error {resp.status}: {text}")
                    return None
                data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result

    except Exception as e:
        logger.error(f"DeepSeek request failed: {e}")
        return None