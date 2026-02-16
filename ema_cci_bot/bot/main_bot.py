import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from bot.config import BOT_TOKEN, ADMIN_ID, MONITORED_PAIRS, TIMEFRAME
from bot.exchange_api import fetch_ohlcv
from bot.indicator_math import add_all_indicators
from bot.strategy_logic import check_signals
from bot.chart_generator import generate_signal_chart

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словарь для хранения времени последнего сигнала по паре (анти-спам)
last_signals = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Бот запущен. Сканер Hyperliquid работает в фоновом режиме 24/7.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer(f"Мониторинг активен.\nПары: {', '.join(MONITORED_PAIRS)}\nТаймфрейм: {TIMEFRAME}\nПоследние сигналы: {last_signals}")


async def process_pair(symbol):
    """Асинхронная задача для обработки одной пары."""
    try:
        # 1. Получаем данные
        df = await fetch_ohlcv(symbol, TIMEFRAME)
        if df is None or len(df) < 300: # Нужно достаточно истории
            return

        # 2. Считаем индикаторы
        df = add_all_indicators(df)

        # 3. Ищем сигнал
        signal = check_signals(df, symbol)

        if signal:
            # Анти-спам: проверяем, не отправляли ли мы этот сигнал только что
            last_time = last_signals.get(symbol)
            if last_time == signal['timestamp']:
                return # Сигнал уже был отправлен на этой свече

            logger.info(f"Найден сигнал: {signal}")
            
            # 4. Генерируем картинку
            chart_buf = generate_signal_chart(df, signal)
            photo_file = BufferedInputFile(chart_buf.read(), filename=f"{symbol}_signal.png")

            # 5. Формируем текст сообщения с Emoji
            emoji_side = "🟢 LONG" if signal['side'] == "LONG" else "🔴 SHORT"
            msg_text = (
                f"{emoji_side} Сигнал по #{symbol}\n\n"
                f"📊 Использована EMA: **{signal['used_ema']}** (Score: {signal['ema_score']})\n\n"
                f"🎯 **ТВХ (Entry):** {signal['entry']:.4f}\n"
                f"🛑 **Стоп-Лосс (SL):** {signal['sl']:.4f}\n\n"
                f"💰 **TP 1 (50%):** {signal['tp1']:.4f}\n"
                f"💰 **TP 2 (100%):** {signal['tp2']:.4f}\n\n"
                f"⏰ Время свечи: {signal['timestamp'].strftime('%Y-%m-%d %H:%M UTC')}"
            )

            # 6. Отправляем в Telegram
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo_file,
                caption=msg_text,
                parse_mode="Markdown"
            )
            
            # Запоминаем время сигнала
            last_signals[signal['symbol']] = signal['timestamp']

    except Exception as e:
        logger.error(f"Ошибка при обработке {symbol}: {e}", exc_info=True)


async def scanner_loop():
    """Бесконечный цикл сканера."""
    logger.info("Сканер запущен.")
    while True:
        # Вычисляем время до следующего 15-минутного закрытия (00, 15, 30, 45 минут)
        now = datetime.utcnow()
        next_quarter = (now.minute // 15 + 1) * 15
        if next_quarter >= 60:
            next_run = now.replace(hour=now.hour + 1, minute=0, second=5, microsecond=0)
        else:
            next_run = now.replace(minute=next_quarter, second=5, microsecond=0)
        
        wait_seconds = (next_run - now).total_seconds()
        logger.info(f"Ждем следующей 15-минутной свечи {wait_seconds:.0f} секунд...")
        
        # Спим до закрытия свечи + 5 секунд задержки на всякий случай
        await asyncio.sleep(wait_seconds)
        
        logger.info("Начинаем сканирование пар...")
        # Создаем задачи для всех пар и запускаем их параллельно
        tasks = [process_pair(symbol) for symbol in MONITORED_PAIRS]
        await asyncio.gather(*tasks)
        logger.info("Сканирование завершено.")


async def main():
    # Запускаем бота и сканер параллельно
    await asyncio.gather(
        dp.start_polling(bot),
        scanner_loop()
    )

if __name__ == "__main__":
    asyncio.run(main())