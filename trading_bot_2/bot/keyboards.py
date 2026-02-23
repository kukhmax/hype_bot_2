"""
Клавиатуры Telegram бота.
Ключевое: signal_action_kb() — кнопки подтверждения/пропуска сигнала.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ─── Главное меню ─────────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="➕ Добавить пару", callback_data="add_pair"),
        InlineKeyboardButton(text="⏹ Стоп пара",  callback_data="stop_pair_menu"),
    )
    b.row(
        InlineKeyboardButton(text="⏹ Останов. все", callback_data="stop_all"),
        InlineKeyboardButton(text="⚙️ Настройки",   callback_data="settings"),
    )
    b.row(InlineKeyboardButton(text="📊 Статус", callback_data="status"))
    return b.as_markup()


# ─── Выбор пары и ТФ ──────────────────────────────────────────────────────────

def popular_pairs_kb() -> InlineKeyboardMarkup:
    """Быстрый выбор популярных пар MEXC Futures."""
    pairs = [
        ("TON", "TON_USDT"), ("ETH", "ETH_USDT"),
        ("SOL", "SOL_USDT"), ("BNB", "BNB_USDT"),
        ("XRP", "XRP_USDT"), ("DOGE", "DOGE_USDT"),
    ]
    b = InlineKeyboardBuilder()
    row = []
    for label, pair in pairs:
        row.append(InlineKeyboardButton(text=label, callback_data=f"pair_{pair}"))
        if len(row) == 3:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    b.row(InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="pair_custom"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    return b.as_markup()

def timeframe_select_kb(pair: str) -> InlineKeyboardMarkup:
    """Выбор таймфрейма после выбора пары."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="1m + 5m", callback_data=f"tf_{pair}_1m_5m"),
        InlineKeyboardButton(text="5m + 15m", callback_data=f"tf_{pair}_5m_15m"),
    )
    b.row(
        InlineKeyboardButton(text="15m + 1h", callback_data=f"tf_{pair}_15m_1h"),
        InlineKeyboardButton(text="1h + 4h", callback_data=f"tf_{pair}_1h_4h"),
    )
    b.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main"))
    return b.as_markup()

def active_pairs_kb(active_pairs: list[str]) -> InlineKeyboardMarkup:
    """Выбор запущенной пары для остановки."""
    b = InlineKeyboardBuilder()
    for pair in active_pairs:
        b.row(InlineKeyboardButton(text=f"⏹ {pair}", callback_data=f"stop_{pair}"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    return b.as_markup()


# ─── Настройки ────────────────────────────────────────────────────────────────

def settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📉 Мин. паттернов", callback_data="set_min_patterns"))
    b.row(InlineKeyboardButton(text="🎯 Мин. confidence AI", callback_data="set_min_conf"))
    b.row(InlineKeyboardButton(text="⏱ Таймаут сигнала", callback_data="set_timeout"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    return b.as_markup()


def min_patterns_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="2️⃣ 2 паттерна", callback_data="mp_2"),
        InlineKeyboardButton(text="3️⃣ 3 паттерна", callback_data="mp_3"),
        InlineKeyboardButton(text="4️⃣ 4 паттерна", callback_data="mp_4"),
    )
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="settings"))
    return b.as_markup()


def min_confidence_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="50%", callback_data="mc_50"),
        InlineKeyboardButton(text="60%", callback_data="mc_60"),
        InlineKeyboardButton(text="70%", callback_data="mc_70"),
        InlineKeyboardButton(text="80%", callback_data="mc_80"),
    )
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="settings"))
    return b.as_markup()


def confirm_timeout_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="3 мин",  callback_data="ct_180"),
        InlineKeyboardButton(text="5 мин",  callback_data="ct_300"),
        InlineKeyboardButton(text="10 мин", callback_data="ct_600"),
    )
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="settings"))
    return b.as_markup()


# ─── Подтверждение сигнала ────────────────────────────────────────────────────

def signal_action_kb(signal_id: str) -> InlineKeyboardMarkup:
    """
    Главная клавиатура для входа.
    signal_id — уникальный ID сигнала (timestamp), чтобы не было конфликтов.
    """
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="✅ ВОЙТИ В СДЕЛКУ",
            callback_data=f"sig_confirm_{signal_id}"
        ),
    )
    b.row(
        InlineKeyboardButton(
            text="📋 Детали сигнала",
            callback_data=f"sig_details_{signal_id}"
        ),
        InlineKeyboardButton(
            text="⏭ Пропустить",
            callback_data=f"sig_skip_{signal_id}"
        ),
    )
    return b.as_markup()


def signal_confirmed_kb() -> InlineKeyboardMarkup:
    """После подтверждения — кнопки управления сделкой."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📊 Статус", callback_data="status"),
        InlineKeyboardButton(text="🏠 Меню",   callback_data="back_main"),
    )
    return b.as_markup()


def signal_skipped_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🏠 Меню", callback_data="back_main"))
    return b.as_markup()
