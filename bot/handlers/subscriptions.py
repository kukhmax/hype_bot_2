from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

async def list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список активных подписок пользователя"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    redis_service = context.bot_data['redis_service']
    subscriptions = await redis_service.get_user_subscriptions(user_id)
    
    if not subscriptions:
        text = "📭 У вас нет активных подписок.\n\nНажмите 'Подписаться' чтобы добавить токен для отслеживания."
        
        keyboard = [[
            InlineKeyboardButton("📝 Подписаться", callback_data="subscribe"),
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")
        ]]
        
        if query:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        return
    
    # Группируем подписки по токенам
    text = "📋 **Ваши активные подписки:**\n\n"
    
    for sub in subscriptions:
        created_raw = getattr(sub, "created_at", None)
        created_dt = None
        if isinstance(created_raw, datetime):
            created_dt = created_raw
        elif isinstance(created_raw, str):
            try:
                created_dt = datetime.fromisoformat(created_raw)
            except ValueError:
                created_dt = None
        created_str = created_dt.strftime('%d.%m %H:%M') if created_dt else "неизвестно"
        text += f"• **{sub.token}** | {sub.timeframe}\n"
        text += f"  └ Подписано: {created_str}\n\n"
    
    text += "Выберите подписку для управления или добавьте новую:"
    
    # Создаем кнопки для каждой подписки + кнопка добавления
    keyboard = []
    for sub in subscriptions:
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {sub.token} {sub.timeframe}",
                callback_data=f"unsubscribe_{sub.token}_{sub.timeframe}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("📝 Добавить", callback_data="subscribe"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def unsubscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отписки от токена"""
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data (формат: unsubscribe_TOKEN_TIMEFRAME)
    data = query.data.replace("unsubscribe_", "")
    token, timeframe = data.split("_")
    
    user_id = update.effective_user.id
    redis_service = context.bot_data['redis_service']
    bot_instance = context.bot_data['bot_instance']
    
    # Удаляем подписку
    await redis_service.remove_subscription(user_id, token, timeframe)
    
    # Проверяем, нужно ли останавливать обработку токена
    remaining = await redis_service._check_token_subscribers(token, timeframe)
    if not remaining:
        # Останавливаем обработчик для этого токена
        key = f"{token}_{timeframe}"
        if key in bot_instance.active_tasks:
            bot_instance.active_tasks[key].cancel()
            del bot_instance.active_tasks[key]
            logger.info(f"Stopped processor for {key} (no subscribers left)")
    
    # Подтверждаем удаление
    await query.answer(f"✅ Подписка на {token} {timeframe} удалена")
    
    # Показываем обновленный список
    await list_subscriptions(update, context)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    query = update.callback_query
    await query.answer()
    
    welcome_text = """
🚀 **HyperLiquid EMA+ADX Trading Bot**

Стратегия основана на пробое канала EMA20 с подтверждением ADX:

**LONG условия:**
• Цена > EMA20(High)
• ADX > 20
• Цена > предыдущего максимума (выше EMA20)

**SHORT условия:**
• Цена < EMA20(Low)
• ADX > 20
• Цена < предыдущего минимума (ниже EMA20)

🤖 **ИИ-подтверждение:** Каждый сигнал проверяется DeepSeek AI для повышения точности

Выберите действие:
    """
    
    keyboard = [
        [
            InlineKeyboardButton("📝 Подписаться", callback_data="subscribe"),
            InlineKeyboardButton("📋 Активные подписки", callback_data="list_subs")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def subscription_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус конкретной подписки"""
    # Этот хендлер можно вызвать при нажатии на название токена
    # (расширение функциональности)
    pass
