"""
Fibo Bot — Telegram Service.

Асинхронный бот на эта базе aiogram 3.x.
- Отправка торговых сигналов с прикрепленным графиком (фото).
- Команды управления: /start, /status, /pause, /resume.
- Форматирование текста сигнала (HTML).
"""

import os
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton

from config import config
from engines.strategy_engine import TradeSignal
from utils.logger import get_logger
from utils.redis_manager import redis_manager
from utils.db_manager import db_manager

logger = get_logger("telegram_bot")


class TelegramService:
    """Сервис для работы с Telegram API."""

    def __init__(self):
        self.bot = None
        self.dp = None
        # admin_ids в конфиге - это список строк
        self.admin_ids = [int(aid) for aid in config.telegram.admin_ids if aid]
        self.admin_id = self.admin_ids[0] if self.admin_ids else 0

        if not config.telegram.token:
            logger.warning("[Telegram] Токен не настроен! Уведомления отключены.")
            return

        self.bot = Bot(
            token=config.telegram.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()

        # Регистрация хэндлеров
        self.dp.message(Command("start"))(self.cmd_start)
        self.dp.message(Command("status"))(self.cmd_status)
        self.dp.message(Command("pause"))(self.cmd_pause)
        self.dp.message(Command("resume"))(self.cmd_resume)

        # Регистрация хэндлеров меню (текстовые кнопки)
        self.dp.message(F.text == "📊 Статус")(self.cmd_status)
        self.dp.message(F.text == "⏸ Пауза")(self.cmd_pause)
        self.dp.message(F.text == "▶️ Возобновить")(self.cmd_resume)

        logger.info("[Telegram] Бот инициализирован")

    def _get_main_keyboard(self) -> ReplyKeyboardMarkup:
        """Создает основную клавиатуру с кнопками."""
        keyboard = [
            [
                KeyboardButton(text="📊 Статус"),
            ],
            [
                KeyboardButton(text="▶️ Возобновить"),
                KeyboardButton(text="⏸ Пауза"),
            ]
        ]
        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="Управление ботом..."
        )

    async def start(self):
        """Запуск поллинга сообщений."""
        if not self.bot:
            return
        logger.info("[Telegram] Запуск поллинга...")
        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"[Telegram] Ошибка поллинга: {e}")

    async def stop(self):
        """Остановка поллинга."""
        if not self.bot:
            return
        logger.info("[Telegram] Остановка бота...")
        await self.bot.session.close()

    # ─── Хэндлеры команд ──────────────────────────────────────────────────

    async def _check_admin(self, message: types.Message) -> bool:
        """Проверка, что команду вызвал админ (проверка по всему списку)."""
        if message.from_user.id not in self.admin_ids:
            logger.warning(f"Unauthorized access from USER {message.from_user.id}")
            await message.reply("У вас нет доступа к управлению ботом.")
            return False
        return True

    async def cmd_start(self, message: types.Message):
        """Команда /start"""
        if not await self._check_admin(message):
            return
        
        text = (
            "🚀 <b>Fibo Bot запущен!</b>\n\n"
            f"Биржа: <b>MEXC {config.exchange.market_type}</b>\n"
            f"Пары: <b>{', '.join(config.trading.symbols)}</b>\n"
            f"Таймфреймы: <b>{', '.join(config.trading.timeframes)}</b>\n\n"
            "Используйте кнопки меню внизу для управления 🔽"
        )
        await message.reply(text, reply_markup=self._get_main_keyboard())

    async def cmd_status(self, message: types.Message):
        """Команда /status — статистика за день и state."""
        if not await self._check_admin(message):
            return

        paused = await redis_manager.is_paused()
        state_str = "⏸ ПАУЗА" if paused else "▶️ АКТИВЕН"

        try:
            stats = await db_manager.get_performance(days=1)
            total = stats.get('total_signals', 0)
            wins = stats.get('wins', 0)
            losses = stats.get('losses', 0)
            wr = stats.get('winrate', 0.0)
            pnl = stats.get('total_pnl', 0.0)

            stats_str = (
                f"📊 <b>Статистика за 24ч:</b>\n"
                f"Сигналы: {total}\n"
                f"Win/Loss: {wins}/{losses} (WR: {wr}%)\n"
                f"PnL: <b>{pnl:.2f}%</b>"
            )
        except Exception as e:
            logger.error(f"[Telegram] Ошибка БД для /status: {e}")
            stats_str = "Статистика временно недоступна."

        text = (
            f"🤖 <b>Статус Fibo Bot</b>\n\n"
            f"Состояние: <b>{state_str}</b>\n"
            f"Пары: {', '.join(config.trading.symbols)}\n"
            f"Риск: {config.trading.risk_per_trade * 100}%\n\n"
            f"{stats_str}"
        )
        await message.reply(text)

    async def cmd_pause(self, message: types.Message):
        """Команда /pause"""
        if not await self._check_admin(message):
            return
        await redis_manager.set_paused(True)
        await message.reply("⏸ Торговые сигналы ПРИОСТАНОВЛЕНЫ.")
        logger.info("[Telegram] Бот поставлен на паузу через команду /pause")

    async def cmd_resume(self, message: types.Message):
        """Команда /resume"""
        if not await self._check_admin(message):
            return
        await redis_manager.set_paused(False)
        await message.reply("▶️ Торговые сигналы ВОЗОБНОВЛЕНЫ.")
        logger.info("[Telegram] Бот возобновлен через команду /resume")

    # ─── Форматирование и отправка сигналов ───────────────────────────────

    def format_signal(self, signal: TradeSignal) -> str:
        """HTML Форматирование текста сигнала."""
        
        # Эмодзи направления
        direction_emoji = "🟢 LONG" if signal.direction == "LONG" else "🔴 SHORT"
        
        # Режим
        regime_map = {
            "TREND_UP": "📈 Trend UP",
            "TREND_DOWN": "📉 Trend DOWN",
            "RANGE": "🔄 Range"
        }
        regime_str = regime_map.get(signal.regime, signal.regime)
        
        # Маржа (может быть добавлена из Risk Engine в explanation)
        # Парсим, если есть
        margin_str = ""
        explanation_lines = signal.explanation.split('\n')
        clean_explanation = []
        for line in explanation_lines:
            if line.startswith("Рекомендуемая маржа:"):
                margin_str = f"💰 {line}\n"
            else:
                clean_explanation.append(line)
        
        # Время
        time_str = datetime.fromtimestamp(signal.timestamp, tz=timezone.utc).strftime("%H:%M:%S UTC")

        text = (
            f"{direction_emoji} <b>{signal.symbol}</b> | {signal.timeframe}\n"
            f"🕒 {time_str}\n\n"
            f"🎯 Setup: <b>{signal.setup}</b>\n"
            f"📊 Regime: {regime_str}\n\n"
            f"📥 <b>Entry</b>: {signal.entry_low:.5g} - {signal.entry_high:.5g}\n"
            f"🛑 <b>Stop Loss</b>: {signal.stop_loss:.5g}\n"
        )
        
        if signal.tp1 > 0:
            text += f"🎯 <b>TP1</b>: {signal.tp1:.5g}\n"
        if signal.tp2 > 0:
            text += f"🎯 <b>TP2</b>: {signal.tp2:.5g}\n"
        if signal.tp3 > 0:
            text += f"🎯 <b>TP3</b>: {signal.tp3:.5g}\n"
            
        text += (
            f"\n⚖️ Risk:Reward = <b>{signal.risk_reward}</b>\n"
            f"{margin_str}\n" # Добавляем маржу отдельной строкой
            f"📝 <b>Детали:</b>\n<i>"
        )
        
        for line in clean_explanation:
            text += f"• {line}\n"
            
        text += "</i>"
        
        # Добавим ML Prob, если есть
        if signal.probability > 0:
            text += f"\n\n🤖 <b>ML AI Probability: {signal.probability:.1f}%</b>"
            
        return text

    async def broadcast_signal(self, signal: TradeSignal):
        """Отправка сигнала админу (сообщение + картинка)."""
        if not self.bot or not self.admin_id:
            logger.warning("[Telegram] Сигнал не отправлен (бот или admin_id не настроен)")
            return
            
        text = self.format_signal(signal)
        
        try:
            if hasattr(signal, 'chart_path') and signal.chart_path and os.path.exists(signal.chart_path):
                # Отправляем фото с подписью
                photo = FSInputFile(signal.chart_path)
                await self.bot.send_photo(
                    chat_id=self.admin_id,
                    photo=photo,
                    caption=text
                )
                logger.info("[Telegram] ✅ Сигнал с графиком успешно отправлен в TG")
            else:
                # Только текст
                await self.bot.send_message(
                    chat_id=self.admin_id,
                    text=text
                )
                logger.info("[Telegram] ✅ Текстовый сигнал отправлен в TG (без графика)")
                
        except Exception as e:
            logger.error(f"[Telegram] ❌ Ошибка отправки сигнала в TG: {e}")

# Глобальный сервис
telegram_service = TelegramService()
