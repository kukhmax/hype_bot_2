import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from bot.handlers.start import start, button_handler, handle_subscription_input, cancel_handler
from bot.handlers.subscriptions import list_subscriptions, unsubscribe_handler
from bot.services.hyperliquid import HyperLiquidWebSocket
from bot.services.strategy import EMAStrategy
from bot.services.deepseek import DeepSeekService
from bot.services.redis_service import RedisService

# Настройка логирования (консоль + файл .log)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("BOT_LOG_FILE", "logs/bot.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

load_dotenv()

class TradingBot:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.hyperliquid = HyperLiquidWebSocket()
        self.strategy = EMAStrategy()
        self.deepseek = DeepSeekService()
        self.redis = RedisService()
        
        self.active_tasks = {}
        self.candles_ready = set()
        self.candle_stats_task = None
    
    async def initialize(self):
        """Инициализация сервисов"""
        await self.redis.connect()
        await self.hyperliquid.connect()
        
        # Восстанавливаем активные подписки
        await self.restore_subscriptions()
        if self.candle_stats_task is None:
            self.candle_stats_task = asyncio.create_task(self.log_candle_stats_loop())
    
    async def restore_subscriptions(self):
        """Восстановление подписок после перезапуска"""
        subscriptions = await self.redis.get_all_active_subscriptions()
        if not subscriptions:
            logger.info("No active subscriptions found on restore")
        else:
            logger.info(
                "Restoring %d active subscriptions", 
                len(subscriptions),
            )
        
        # Группируем по токенам
        for sub in subscriptions:
            key = f"{sub.token}_{sub.timeframe}"
            logger.info(
                "Restoring subscription: user=%s token=%s timeframe=%s key=%s",
                getattr(sub, "user_id", None),
                sub.token,
                sub.timeframe,
                key,
            )
            
            if key not in self.active_tasks:
                # Создаем задачу для обработки этого токена
                task = asyncio.create_task(
                    self.process_token(sub.token, sub.timeframe)
                )
                self.active_tasks[key] = task
    
    async def process_token(self, token: str, timeframe: str):
        """Обработка сигналов для токена"""
        logger.info(f"Starting processor for {token} {timeframe}")
        
        # Подписываемся на свечи
        await self.hyperliquid.subscribe_candles(
            token, 
            timeframe, 
            lambda data: self.check_signal(token, timeframe, data)
        )
        
        # Держим задачу живой
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info(f"Processor for {token} {timeframe} cancelled")
    
    async def check_signal(self, token: str, timeframe: str, candle_data: dict):
        """Проверка сигнала на новых свечах"""
        key = f"{token}_{timeframe}"
        highs = self.hyperliquid.get_candle_array(token, timeframe, 'highs')
        lows = self.hyperliquid.get_candle_array(token, timeframe, 'lows')
        closes = self.hyperliquid.get_candle_array(token, timeframe, 'closes')
        timestamps = self.hyperliquid.get_candle_array(token, timeframe, 'timestamps')
        
        candles_count = len(closes)
        if candles_count < self.strategy.candles_needed:
            if key not in self.candles_ready:
                logger.info(
                    "Candles not ready for %s: have=%d need=%d",
                    key,
                    candles_count,
                    self.strategy.candles_needed,
                )
            return
        
        if key not in self.candles_ready:
            last_ts = timestamps[-1] if len(timestamps) else None
            try:
                last_dt = datetime.fromtimestamp(last_ts / 1000) if last_ts is not None else None
                logger.info(
                    "Candles ready for %s: have=%d need=%d last_ts=%s",
                    key,
                    candles_count,
                    self.strategy.candles_needed,
                    last_dt.isoformat() if last_dt else last_ts,
                )
            except Exception:
                logger.info(
                    "Candles ready for %s: have=%d need=%d last_ts_raw=%s",
                    key,
                    candles_count,
                    self.strategy.candles_needed,
                    last_ts,
                )
            self.candles_ready.add(key)
        
        logger.info("Running strategy checks for %s: candles=%d", key, candles_count)
        
        label = f"{token}_{timeframe}"
        is_long, long_setup = self.strategy.check_long_setup(highs, lows, closes, timestamps, context=label)
        if is_long:
            await self.handle_signal(token, timeframe, long_setup)
        
        is_short, short_setup = self.strategy.check_short_setup(highs, lows, closes, timestamps, context=label)
        if is_short:
            await self.handle_signal(token, timeframe, short_setup)
    
    async def handle_signal(self, token: str, timeframe: str, setup_info: dict):
        """Обработка найденного сигнала"""
        logger.info(
            "Signal detected (raw setup): %s %s side=%s entry=%.4f adx=%.2f plus_di=%.2f minus_di=%.2f",
            token,
            timeframe,
            setup_info.get('side'),
            float(setup_info.get('entry')),
            float(setup_info.get('adx')),
            float(setup_info.get('plus_di')),
            float(setup_info.get('minus_di')),
        )
        
        # Запрашиваем подтверждение у DeepSeek
        signal = await self.deepseek.confirm_signal(setup_info, token, timeframe)
        
        if not signal:
            logger.warning(f"No confirmation from DeepSeek for {token}")
            return
        
        logger.info(
            "Signal confirmed by DeepSeek: %s %s side=%s confidence=%.2f entry_range=[%.4f, %.4f]",
            token,
            timeframe,
            signal.side,
            float(signal.confidence),
            float(signal.entry_min),
            float(signal.entry_max),
        )
        
        # Сохраняем сигнал
        await self.redis.save_signal(signal)
        
        # Находим всех подписчиков на этот токен
        subscriptions = await self.redis.get_all_active_subscriptions()
        subscribers = [
            sub for sub in subscriptions 
            if sub.token == token and sub.timeframe == timeframe
        ]
        
        # Отправляем уведомления
        app = self.application
        for sub in subscribers:
            await self.send_signal_notification(app, sub.user_id, signal)
    
    async def log_candle_stats_loop(self):
        """Периодический лог количества свечей и временного диапазона"""
        while True:
            try:
                for key, data in self.hyperliquid.candle_data.items():
                    ts = data.get('timestamps') or []
                    if not ts:
                        continue
                    count = len(ts)
                    start_ts = ts[0]
                    end_ts = ts[-1]
                    try:
                        start_dt = datetime.fromtimestamp(start_ts / 1000)
                        end_dt = datetime.fromtimestamp(end_ts / 1000)
                        logger.info(
                            "Candle summary %s: count=%d range=%s -> %s",
                            key,
                            count,
                            start_dt.isoformat(),
                            end_dt.isoformat(),
                        )
                    except Exception:
                        logger.info(
                            "Candle summary %s: count=%d ts_from=%s ts_to=%s",
                            key,
                            count,
                            start_ts,
                            end_ts,
                        )
                await asyncio.sleep(1800)
            except asyncio.CancelledError:
                logger.info("Candle stats loop cancelled")
                break
            except Exception as e:
                logger.error("Error in candle stats loop: %s", e)
                await asyncio.sleep(60)
    
    async def send_signal_notification(self, app: Application, user_id: int, signal):
        """Отправка сигнала пользователю"""
        message = f"""
🚨 **ТОРГОВЫЙ СИГНАЛ** 🚨

**{signal.token}** | {signal.timeframe}
**{signal.side}** 🔥

🤖 **Уверенность ИИ:** {signal.confidence:.0f}%

📊 **Вход:** ${signal.entry_min:.2f} - ${signal.entry_max:.2f}
🛑 **Стоп:** ${signal.stop_loss:.2f}
✅ **TP1:** ${signal.take_profit_1:.2f}
✅ **TP2:** ${signal.take_profit_2:.2f}

📝 **Анализ:**
{signal.description}

⏰ {signal.timestamp.strftime('%H:%M:%S')}
        """
        
        try:
            await app.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
    
    async def run(self):
        """Запуск бота в уже существующем event loop"""
        self.application = Application.builder().token(self.telegram_token).build()
        self.application.bot_data['redis_service'] = self.redis
        self.application.bot_data['hyperliquid'] = self.hyperliquid
        self.application.bot_data['strategy'] = self.strategy
        self.application.bot_data['deepseek'] = self.deepseek
        self.application.bot_data['bot_instance'] = self

        self.application.add_handler(CommandHandler("start", start))
        self.application.add_handler(CommandHandler("cancel", cancel_handler))
        self.application.add_handler(CallbackQueryHandler(button_handler))
        self.application.add_handler(CallbackQueryHandler(unsubscribe_handler, pattern="^unsubscribe_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subscription_input))

        await self.initialize()

        await self.application.initialize()
        await self.application.start()
        logger.info("Bot started")

        try:
            # держим цикл живым
            while True:
                await asyncio.sleep(3600)
        finally:
            await self.application.stop()
            await self.application.shutdown()
    
    async def shutdown(self):
        """Корректное завершение"""
        # Отменяем все задачи
        for task in self.active_tasks.values():
            task.cancel()
        
        # Закрываем соединения
        await self.application.stop()
        await self.application.shutdown()
        logger.info("Bot stopped")

def main():
    """Точка входа"""
    bot = TradingBot()
    application = Application.builder() \
        .token(bot.telegram_token) \
        .post_init(lambda app: bot.initialize()) \
        .build()
    # bot_data
    application.bot_data['redis_service'] = bot.redis
    application.bot_data['hyperliquid'] = bot.hyperliquid
    application.bot_data['strategy'] = bot.strategy
    application.bot_data['deepseek'] = bot.deepseek
    application.bot_data['bot_instance'] = bot
    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    # Логируем все callback перед основной обработкой
    async def log_callback(update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = getattr(update.effective_user, "id", None)
        data = getattr(q, "data", None)
        logger.info(f"Callback received: data={data} user={uid}")
    application.add_handler(CallbackQueryHandler(log_callback, block=False), group=-1)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(unsubscribe_handler, pattern="^unsubscribe_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subscription_input))
    # diagnostics: log all updates детально
    async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = getattr(update.effective_user, "id", None)
        chat_id = getattr(update.effective_chat, "id", None)
        has_cb = hasattr(update, "callback_query") and update.callback_query is not None
        has_msg = hasattr(update, "message") and update.message is not None
        logger.info(
            "Update received: type=%s user=%s chat=%s has_message=%s has_callback=%s",
            type(update).__name__,
            uid,
            chat_id,
            has_msg,
            has_cb,
        )
    application.add_handler(MessageHandler(filters.ALL, log_update), group=100)
    # errors
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error("Unhandled error: %s", context.error, exc_info=context.error)
    application.add_error_handler(error_handler)
    # run polling (blocks)
    logger.info("Starting Application.run_polling() with ALL update types")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
