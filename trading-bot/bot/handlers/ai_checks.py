"""
Обработчики для ручных проверок сигналов через AI (DeepSeek и Gemini).
"""
import html
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from core.redis_client import redis_client
from core.deepseek_client import analyze_setup
from core.gemini_client import analyze_setup_with_gemini
from core.indicators import SetupResult

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("ds_check:"))
async def handle_deepseek_check(callback: CallbackQuery):
    """Хендлер проверки сигнала через DeepSeek."""
    _, token, tf = callback.data.split(":", 2)
    user_id = callback.from_user.id

    await callback.answer("⏳ Запрашиваю анализ DeepSeek...", show_alert=False)

    setup_data = await redis_client.get_latest_setup(user_id, token, tf)
    if not setup_data:
        await callback.message.reply("⚠️ Данные сетапа устарели или не найдены.")
        return

    setup = SetupResult(**setup_data)
    
    # Редактируем сообщение, чтобы показать процесс
    loading_msg = await callback.message.reply("⏳ <i>DeepSeek анализирует сетап...</i>", parse_mode="HTML")

    analysis = await analyze_setup(token, tf, setup)
    if not analysis:
        await loading_msg.edit_text("❌ Ошибка при запросе к DeepSeek.")
        return

    verdict = analysis.get("verdict", "SKIP")
    confidence = analysis.get("confidence", 0)
    entry_low = analysis.get("entry_low", setup.close_last)
    entry_high = analysis.get("entry_high", setup.close_last)
    sl = analysis.get("stop_loss", 0)
    tp1 = analysis.get("take_profit_1", 0)
    tp2 = analysis.get("take_profit_2", 0)
    rr1 = analysis.get("rr_ratio_tp1", 0)
    rr2 = analysis.get("rr_ratio_tp2", 0)
    desc = analysis.get("analysis", "")
    skip_reason = analysis.get("skip_reason", "")

    if verdict == "SKIP":
        st_color = "❌"
        verdict_str = "ОТКЛОНЕН (Ложный пробой/Ловушка)"
    elif verdict == "AGGRESSIVE_ENTRY":
        st_color = "⚡️"
        verdict_str = "Агрессивный вход"
    else:
        st_color = "⚠️"
        verdict_str = "Осторожный вход"

    msg = (
        f"🧠 <b>DeepSeek Анализ</b> | {token} {tf}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{st_color} <b>Вердикт:</b> {verdict_str}\n"
        f"📊 <b>Уверенность:</b> {confidence}%\n"
        f"\n"
        f"📍 <b>Вход:</b> {entry_low:.4f} – {entry_high:.4f}\n"
        f"🛑 <b>Стоп-лосс:</b> {sl:.4f}\n"
        f"🎯 <b>TP1:</b> {tp1:.4f}  (RR {rr1:.1f}:1)\n"
        f"🎯 <b>TP2:</b> {tp2:.4f}  (RR {rr2:.1f}:1)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <i>{html.escape(skip_reason if verdict == 'SKIP' else desc)}</i>"
    )

    await loading_msg.edit_text(msg, parse_mode="HTML")


@router.callback_query(F.data.startswith("gm_check:"))
async def handle_gemini_check(callback: CallbackQuery):
    """Хендлер проверки сигнала через Gemini."""
    _, token, tf = callback.data.split(":", 2)
    user_id = callback.from_user.id

    await callback.answer("⏳ Запрашиваю отчет Gemini...", show_alert=False)

    setup_data = await redis_client.get_latest_setup(user_id, token, tf)
    if not setup_data:
        await callback.message.reply("⚠️ Данные сетапа устарели или не найдены.")
        return

    setup = SetupResult(**setup_data)
    
    loading_msg = await callback.message.reply("⏳ <i>Gemini пишет отчет...</i>", parse_mode="HTML")

    gemini_report = await analyze_setup_with_gemini(token, tf, setup, setup.close_last)
    if not gemini_report:
        await loading_msg.edit_text("❌ Ошибка при запросе к Gemini.")
        return

    verdict = gemini_report.get("verdict", "SKIP")
    confidence = gemini_report.get("confidence", 0)
    entry_low = gemini_report.get("entry_low", setup.close_last)
    entry_high = gemini_report.get("entry_high", setup.close_last)
    sl = gemini_report.get("stop_loss", 0)
    tp1 = gemini_report.get("take_profit_1", 0)
    tp2 = gemini_report.get("take_profit_2", 0)
    rr1 = gemini_report.get("rr_ratio_tp1", 0)
    rr2 = gemini_report.get("rr_ratio_tp2", 0)
    desc = gemini_report.get("analysis", "")
    skip_reason = gemini_report.get("skip_reason", "")

    if verdict == "SKIP":
        st_color = "❌"
        verdict_str = "ОТКЛОНЕН (Ложный пробой/Ловушка)"
    elif verdict == "AGGRESSIVE_ENTRY":
        st_color = "⚡️"
        verdict_str = "Агрессивный вход"
    else:
        st_color = "⚠️"
        verdict_str = "Осторожный вход"

    msg = (
        f"🤖 <b>Gemini Анализ</b> | {token} {tf}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{st_color} <b>Вердикт:</b> {verdict_str}\n"
        f"📊 <b>Уверенность:</b> {confidence}%\n"
        f"\n"
        f"📍 <b>Вход:</b> {entry_low:.4f} – {entry_high:.4f}\n"
        f"🛑 <b>Стоп-лосс:</b> {sl:.4f}\n"
        f"🎯 <b>TP1:</b> {tp1:.4f}  (RR {rr1:.1f}:1)\n"
        f"🎯 <b>TP2:</b> {tp2:.4f}  (RR {rr2:.1f}:1)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <i>{html.escape(skip_reason if verdict == 'SKIP' else desc)}</i>"
    )

    await loading_msg.edit_text(msg, parse_mode="HTML")
