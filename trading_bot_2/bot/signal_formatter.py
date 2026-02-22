"""
Форматирование MTFContext в Telegram-сообщение.
Показывает 15m сигнал + 1h подтверждение + confluence score.
"""
from core.signal_engine import TradeSignal
from core.multi_tf_engine import MTFContext
from patterns.base import Direction


def format_mtf_signal(ctx: MTFContext) -> str:
    signal = ctx.signal
    is_long = signal.direction == Direction.LONG

    dir_label = "🟢 ЛОНГ" if is_long else "🔴 ШОРТ"
    trend_emoji = {"UP": "📈", "DOWN": "📉", "FLAT": "➡️"}.get(ctx.h1_trend, "➡️")
    conf_bar = _bar(ctx.confluence_score)

    patterns_text = "\n".join(
        f"    ✅ {p.name} — {int(p.strength * 100)}%"
        for p in signal.patterns
    )

    ind = signal.indicators
    ind_15m = (
        f"  ADX `{ind.adx:.1f}` (+DI {ind.plus_di:.0f} / -DI {ind.minus_di:.0f})\n"
        f"  RSI `{ind.rsi:.1f}` · CCI `{ind.cci:.0f}`\n"
        f"  ATR `{signal.atr:.4f}` ({ind.atr_pct:.2f}%)"
    ) if ind else "—"

    gemini_block = ""
    if signal.gemini_analysis:
        gemini_block = (
            f"\n🤖 *AI Gemini:*\n"
            f"_{signal.gemini_analysis}_\n"
        )

    pct_sl  = abs(signal.entry - signal.stop_loss)    / signal.entry * 100
    pct_tp1 = abs(signal.take_profit_1 - signal.entry) / signal.entry * 100
    pct_tp2 = abs(signal.take_profit_2 - signal.entry) / signal.entry * 100

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 *MEXC FUTURES* · `{signal.symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*{dir_label}*  ·  15m сигнал\n\n"
        f"📐 *Вход / SL / TP:*\n"
        f"```\n"
        f"Вход   {signal.entry:.4f}\n"
        f"SL     {signal.stop_loss:.4f}   (-{pct_sl:.2f}%)\n"
        f"TP1    {signal.take_profit_1:.4f}  (+{pct_tp1:.2f}%)\n"
        f"TP2    {signal.take_profit_2:.4f}  (+{pct_tp2:.2f}%)\n"
        f"R/R    1 : {signal.risk_reward}\n"
        f"```\n\n"
        f"🔍 *Паттерны 15m ({len(signal.patterns)}):*\n{patterns_text}\n\n"
        f"📊 *Индикаторы 15m:*\n{ind_15m}\n\n"
        f"{trend_emoji} *Тренд 1h:* `{ctx.h1_trend}`"
        f"  ·  ADX `{ctx.h1_adx:.1f}`  ·  RSI `{ctx.h1_rsi:.1f}`\n"
        f"{'✅ 15m и 1h согласованы' if ctx.tf_agreement else '⚠️ Расхождение TF'}\n"
        f"{gemini_block}\n"
        f"🎯 *Confluence:* {conf_bar} `{ctx.confluence_score}%`\n\n"
        f"⏳ _Подтверди вход или пропусти (5 мин)_"
    )


def format_signal_expired(ctx: MTFContext) -> str:
    return (
        f"⏰ *Сигнал устарел*\n"
        f"`{ctx.signal.symbol}` {ctx.signal.direction.value}"
        f" — время подтверждения истекло."
    )


def format_signal_confirmed(ctx: MTFContext) -> str:
    s = ctx.signal
    is_long = s.direction == Direction.LONG
    return (
        f"✅ *Вход подтверждён!*\n\n"
        f"{'🟢 ЛОНГ' if is_long else '🔴 ШОРТ'} · `{s.symbol}` · MEXC FUTURES\n\n"
        f"📌 Параметры:\n"
        f"• Вход:  `{s.entry:.4f}`\n"
        f"• SL:    `{s.stop_loss:.4f}`\n"
        f"• TP1:   `{s.take_profit_1:.4f}`\n"
        f"• TP2:   `{s.take_profit_2:.4f}`\n\n"
        f"💡 _Закрой 50% на TP1, остаток с трейлингом._"
    )


def format_signal_skipped(ctx: MTFContext) -> str:
    return f"⏭ Пропущено · `{ctx.signal.symbol}` {ctx.signal.direction.value}"


def format_status(symbol: str, is_running: bool, buf_15m_len: int, buf_1h_len: int) -> str:
    status = "🟢 Работает" if is_running else "🔴 Остановлен"
    return (
        f"📊 *Статус бота*\n\n"
        f"Состояние: {status}\n"
        f"Биржа: `MEXC Futures`\n"
        f"Пара: `{symbol}`\n"
        f"Таймфреймы: `15m` (вход) + `1h` (тренд)\n\n"
        f"Буфер 15m: `{buf_15m_len}` свечей\n"
        f"Буфер 1h:  `{buf_1h_len}` свечей\n"
    )


def _bar(score: int) -> str:
    filled = round(score / 10)
    empty  = 10 - filled
    block  = "🟩" if score >= 75 else ("🟨" if score >= 60 else "🟥")
    return block * filled + "⬜" * empty
