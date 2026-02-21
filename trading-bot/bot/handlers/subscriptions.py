from aiogram import Router, F
from aiogram.types import CallbackQuery, Message

from bot.keyboards import main_menu_kb, subscriptions_kb
from bot.messages import SUBS_HEADER, NO_SUBS, UNSUB_SUCCESS, WELCOME
from core.redis_client import redis_client
from core.worker import get_ws_client
from config import config

router = Router()


@router.callback_query(F.data == "my_subs")
async def show_subscriptions(callback: CallbackQuery):
    subs = await redis_client.get_subscriptions(callback.from_user.id)
    if not subs:
        await callback.message.edit_text(NO_SUBS, reply_markup=main_menu_kb())
    else:
        await callback.message.edit_text(
            SUBS_HEADER,
            parse_mode="HTML",
            reply_markup=subscriptions_kb(subs),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("unsub:"))
async def handle_unsubscribe(callback: CallbackQuery):
    _, token, tf = callback.data.split(":", 2)
    user_id = callback.from_user.id

    await redis_client.remove_subscription(user_id, token, tf)

    ws = get_ws_client()
    if ws:
        await ws.unsubscribe(user_id, token, tf)

    tf_display = config.TF_DISPLAY.get(tf, tf)
    await callback.answer(f"✅ {token} {tf_display} удалён", show_alert=False)

    # Обновляем список
    subs = await redis_client.get_subscriptions(user_id)
    if not subs:
        await callback.message.edit_text(NO_SUBS, reply_markup=main_menu_kb())
    else:
        await callback.message.edit_text(
            SUBS_HEADER,
            parse_mode="HTML",
            reply_markup=subscriptions_kb(subs),
        )


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        WELCOME, parse_mode="HTML", reply_markup=main_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()