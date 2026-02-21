from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import config


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Подписаться", callback_data="subscribe")
    builder.button(text="📋 Активные подписки", callback_data="my_subs")
    builder.adjust(1)
    return builder.as_markup()


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
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="back_main")
    return builder.as_markup()