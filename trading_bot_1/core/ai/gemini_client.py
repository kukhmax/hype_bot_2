import json
import asyncio
from typing import Tuple, Dict, Any
import google.generativeai as genai

from core.config import settings
from core.logger import setup_logger

logger = setup_logger("gemini_client")

class GeminiClient:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.is_configured = False
        
        if self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
                self.is_configured = True
                logger.info("Gemini API успешно сконфигурирован.")
            except Exception as e:
                logger.error(f"Ошибка конфигурации Gemini: {e}")
        else:
            logger.warning("GEMINI_API_KEY не установлен. ИИ верификация отключена.")

    def _build_prompt(self, symbol: str, timeframe: int, direction: str, indicators: Dict[str, Any]) -> str:
        """
        Построить промпт для Gemini в зависимости от таймфрейма.
        
        Для коротких таймфреймов (1-4m) — адаптированные правила:
        - EMA 200 не используется как критерий (на 1m = 3.3 часа данных)
        - ADX порог снижен (на 1m ADX естественно ниже)
        - Акцент на RSI, BB Width и моментуме
        
        Для стандартных таймфреймов (5m+) — классические правила.
        """
        
        if timeframe < 5:
            # --- КОРОТКИЕ ТАЙМФРЕЙМЫ (1m - 4m) ---
            return f"""
You are a professional crypto scalper analyzing a proposed futures trade on a VERY SHORT timeframe.
IMPORTANT: This is a {timeframe}-minute chart. Standard trend-following rules DO NOT APPLY here.

Trade Details:
- Symbol: {symbol}
- Timeframe: {timeframe} minutes (SCALPING)
- Proposed Direction: {direction}

Technical Indicators:
- Close Price: {indicators.get('close')}
- RSI (14): {indicators.get('rsi')}
- ADX (14): {indicators.get('adx')}
- EMA 200: {indicators.get('ema_200')} (NOTE: On {timeframe}m this covers only {round(timeframe * 200 / 60, 1)} hours — NOT a reliable trend indicator)
- Bollinger Bands Width %: {indicators.get('bb_width_percent')}

SCALPING RULES (adapted for {timeframe}m):
1. DO NOT use EMA 200 as a trend filter — it is meaningless on {timeframe}m charts.
2. ADX above 15 is sufficient for {timeframe}m (standard threshold of 25 is for 15m+).
3. For LONG: RSI should not be extremely overbought (>80). RSI 50-70 is acceptable.
4. For SHORT: RSI should not be extremely oversold (<20). RSI 30-50 is acceptable.
5. BB Width > 0.5% indicates enough volatility for a scalp trade — this is GOOD.
6. Focus on momentum and volatility, NOT on long-term trend alignment.
7. Be MORE PERMISSIVE than on higher timeframes — scalping relies on quick moves.
8. Only reject trades with CLEAR contradictions (e.g., RSI > 85 for LONG, or zero volatility).

Provide brief reasoning in Russian (1-2 sentences).
Return ONLY JSON: {{"is_approved": bool, "reasoning": "string"}}
"""
        else:
            # --- СТАНДАРТНЫЕ ТАЙМФРЕЙМЫ (5m+) ---
            return f"""
You are a professional crypto trading assistant and risk manager analyzing a proposed futures trade.
Your task is to analyze the technical indicators and decide whether to APPROVE or REJECT the trade signal.

Trade Details:
- Symbol: {symbol}
- Timeframe: {timeframe} minutes
- Proposed Direction: {direction}

Technical Indicators for the latest candle:
- Close Price: {indicators.get('close')}
- RSI (14): {indicators.get('rsi')}
- ADX (14): {indicators.get('adx')}
- EMA 200: {indicators.get('ema_200')}
- Bollinger Bands Width %: {indicators.get('bb_width_percent')}

Rules:
1. If it's a LONG signal, RSI should ideally not be overbought (>70) unless there's a strong breakout (ADX > 25).
2. If it's a SHORT signal, RSI should ideally not be oversold (<30) unless ADX > 25.
3. Consider EMA 200 as a trend filter: LONG is safer above EMA 200, SHORT below.
4. Reject trades that seem too risky based on standard technical analysis.
5. Provide a brief reasoning in Russian (1-2 sentences).

Return ONLY a JSON response in the following format:
{{"is_approved": bool, "reasoning": "string"}}
"""

    async def verify_signal(self, symbol: str, timeframe: int, direction: str, indicators: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.is_configured:
            return True, "API ключ не настроен. Сделка разрешена по умолчанию."
        
        prompt = self._build_prompt(symbol, timeframe, direction, indicators)
        
        logger.info(
            f"[AI] Запрос верификации: {symbol} {direction} {timeframe}m | "
            f"RSI={indicators.get('rsi', 0):.1f} ADX={indicators.get('adx', 0):.1f} "
            f"EMA200={indicators.get('ema_200', 0):.2f} BBW={indicators.get('bb_width_percent', 0):.2f}%"
        )
        
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            result = json.loads(response.text)
            
            is_approved = result.get("is_approved", True)
            reasoning = result.get("reasoning", "Без объяснений.")
            
            action = "✅ ОДОБРЕНО" if is_approved else "❌ ОТКЛОНЕНО"
            logger.info(f"[AI] {action}: {reasoning}")
            
            return is_approved, reasoning
            
        except Exception as e:
            logger.error(f"Ошибка запроса к Gemini: {e}")
            return True, f"Ошибка API: {e}. Сделка разрешена."

gemini_client = GeminiClient()
