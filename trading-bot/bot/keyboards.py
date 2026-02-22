from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from config import config


def main_menu_kb() -> InlineKeyboardMarkup:
    """Отображает главное меню бота (Подписаться / Мои подписки)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Подписаться", callback_data="subscribe")
    builder.button(text="📋 Активные подписки", callback_data="my_subs")
    builder.adjust(1)
    return builder.as_markup()

def bottom_menu_kb() -> ReplyKeyboardMarkup:
    """Отображает нижнее закрепленное меню бота с двумя основными кнопками."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Подписаться")
    builder.button(text="📋 Активные подписки")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, persistent=True)


def subscriptions_kb(subs: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Список подписок с кнопкой отмены у каждой."""
    builder = InlineKeyboardBuilder()
    if not subs:
        builder.button(text="(пусто)", callback_data="noop")
    else:
        for token, tf in subs:
            tf_display = config.TF_DISPLAY.get(tf, tf)
            builder.button(
                text=f"❌ {token} | {tf_display}",
                callback_data=f"unsub:{token}:{tf}",
            )
    builder.button(text="⬅️ Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def timeframe_kb() -> InlineKeyboardMarkup:
    """Выбор таймфрейма при подписке."""
    builder = InlineKeyboardBuilder()
    for tf in config.ALLOWED_TIMEFRAMES:
        builder.button(text=config.TF_DISPLAY[tf], callback_data=f"tf:{tf}")
    builder.button(text="❌ Отмена", callback_data="back_main")
    builder.adjust(4)
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    """Только кнопка 'Отмена' для прерывания процесса."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="back_main")
    return builder.as_markup()

def signal_trade_kb(token: str, direction: str, sl: float, tp: float, tf: str = "3m") -> InlineKeyboardMarkup:
    """Кнопки открытия позиции и ручной проверки AI, прикрепляемые к сигналу."""
    builder = InlineKeyboardBuilder()
    # Округляем до 4 знаков, чтобы влезть в 64 байта callback_data
    cb_data = f"trade:{token}:{direction}:{sl:.4f}:{tp:.4f}"
    # Если callback_data слишком большая, Telegram выдаст ошибку, но тут должно хватить
    if len(cb_data.encode("utf-8")) > 64:
        # Fallback без цен, цены будем брать из стейта или не ставить
        cb_data = f"trade:{token}:{direction}:0:0"
        
    builder.button(text="💰 Открыть позицию (Market)", callback_data=cb_data)
    builder.button(text="🧠 Deepseek check", callback_data=f"ds_check:{token}:{tf}")
    builder.button(text="🤖 Gemini check", callback_data=f"gm_check:{token}:{tf}")
    builder.adjust(1, 2)
    return builder.as_markup()