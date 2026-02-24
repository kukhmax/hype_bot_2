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

    async def verify_signal(self, symbol: str, timeframe: int, direction: str, indicators: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.is_configured:
            return True, "API ключ не настроен. Сделка разрешена по умолчанию."
            
        prompt = f"""
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
        3. Reject trades that seem too risky based on standard technical analysis.
        4. Provide a brief reasoning in Russian (1-2 sentences).
        
        Return ONLY a JSON response in the following format:
        {{"is_approved": bool, "reasoning": "string"}}
        """
        
        try:
            # Вызов генерации (в отдельном потоке, чтобы не блочить asyncio loop)
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            
            # Парсинг JSON
            result = json.loads(response.text)
            return result.get("is_approved", True), result.get("reasoning", "Без объяснений.")
            
        except Exception as e:
            logger.error(f"Ошибка запроса к Gemini: {e}")
            return True, f"Ошибка API: {e}. Сделка разрешена."

gemini_client = GeminiClient()
