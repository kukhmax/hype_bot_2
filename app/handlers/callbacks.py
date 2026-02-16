from aiogram import Router, F, types

router = Router()

@router.callback_query(F.data.startswith("open"))
async def open_trade(callback: types.CallbackQuery):
    await callback.answer("Автотрейдинг скоро будет 🚀")
