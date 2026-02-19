import aiohttp
import asyncio
import os
from typing import Dict, Optional
from ..models.signal import Signal

class DeepSeekService:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.api_url = os.getenv("DEEPSEEK_API_URL")
    
    async def confirm_signal(self, setup_info: Dict, token: str, timeframe: str) -> Optional[Dict]:
        """
        Отправка запроса в DeepSeek для подтверждения сигнала
        """
        prompt = self._build_prompt(setup_info, token, timeframe)
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional crypto futures trader. Analyze the setup and provide confirmation, stop loss, and take profit levels."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,
                "max_tokens": 500
            }
            
            try:
                async with session.post(self.api_url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        return self._parse_response(result, setup_info, token, timeframe)
                    else:
                        print(f"DeepSeek API error: {response.status}")
                        return None
            except Exception as e:
                print(f"Error calling DeepSeek: {e}")
                return None
    
    def _build_prompt(self, setup_info: Dict, token: str, timeframe: str) -> str:
        """
        Формирование промпта для DeepSeek на основе нашей стратегии
        """
        return f"""
        CRYPTO FUTURES SIGNAL CONFIRMATION
        
        Token: {token} (USDT perpetual futures)
        Timeframe: {timeframe}
        
        My strategy has detected a potential {setup_info['side']} signal:
        - Current price: ${setup_info['entry']:.2f}
        - ADX: {setup_info.get('adx', 0):.1f}
        - +DI: {setup_info.get('plus_di', 0):.1f}
        - -DI: {setup_info.get('minus_di', 0):.1f}
        
        Please analyze this setup and provide:
        1. Confidence level (0-100%)
        2. Optimal entry range (±0.5% from current price)
        3. Stop loss level (in $, with brief reasoning)
        4. Two take profit levels (TP1: 1:1 risk-reward, TP2: 2:1 risk-reward)
        5. Brief market context and confirmation notes
        
        Consider:
        - Volume profile
        - Key support/resistance levels
        - Overall trend context
        - Any immediate risks
        
        Format the response as JSON:
        {{
            "confidence": 85,
            "entry_min": 50000,
            "entry_max": 50500,
            "stop_loss": 49500,
            "take_profit_1": 51000,
            "take_profit_2": 52000,
            "description": "Brief analysis..."
        }}
        """
    
    def _parse_response(self, api_response: Dict, setup_info: Dict, token: str, timeframe: str) -> Signal:
        """
        Парсинг ответа от DeepSeek в модель Signal
        """
        try:
            content = api_response['choices'][0]['message']['content']
            # В реальном проекте здесь нужно безопасно распарсить JSON
            # Для простоты используем eval (не рекомендуется в продакшне!)
            import json
            data = json.loads(content)
            
            return Signal(
                token=token,
                timeframe=timeframe,
                side=setup_info['side'],
                confidence=data['confidence'],
                entry_min=data['entry_min'],
                entry_max=data['entry_max'],
                stop_loss=data['stop_loss'],
                take_profit_1=data['take_profit_1'],
                take_profit_2=data['take_profit_2'],
                description=data['description'],
                price_at_signal=setup_info['entry'],
                timestamp=datetime.now()
            )
        except Exception as e:
            print(f"Error parsing DeepSeek response: {e}")
            # Fallback сигнал
            return Signal(
                token=token,
                timeframe=timeframe,
                side=setup_info['side'],
                confidence=70.0,
                entry_min=setup_info['entry'] * 0.995,
                entry_max=setup_info['entry'] * 1.005,
                stop_loss=setup_info['entry'] * 0.98,
                take_profit_1=setup_info['entry'] * 1.02,
                take_profit_2=setup_info['entry'] * 1.04,
                description="Signal detected, but AI confirmation failed",
                price_at_signal=setup_info['entry'],
                timestamp=datetime.now()
            )