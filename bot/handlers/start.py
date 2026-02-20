import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import logging
from .subscriptions import list_subscriptions, unsubscribe_handler, back_to_menu

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    logger.info("start handler: user=%s chat=%s", getattr(update.effective_user, 'id', None), getattr(update.effective_chat, 'id', None))
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
    
    inline_keyboard = [
        [
            InlineKeyboardButton("📝 Подписаться", callback_data="subscribe"),
            InlineKeyboardButton("📋 Активные подписки", callback_data="list_subs")
        ]
    ]
    
    reply_keyboard = [
        [
            KeyboardButton("📝 Подписаться"),
            KeyboardButton("📋 Активные подписки"),
        ]
    ]
    
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    bottom_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=inline_markup, parse_mode='Markdown')
        await update.message.reply_text("Выберите действие с помощью кнопок внизу.", reply_markup=bottom_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=inline_markup, parse_mode='Markdown')
        await update.effective_chat.send_message("Выберите действие с помощью кнопок внизу.", reply_markup=bottom_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    logger.info("button_handler: data=%s user=%s", getattr(update.callback_query, 'data', None), getattr(update.effective_user, 'id', None))
    query = update.callback_query
    await query.answer()
    
    if query.data == "subscribe":
        await query.edit_message_text(
            "📝 **Добавление подписки**\n\n"
            "Введите токен и таймфрейм через пробел\n"
            "Например: `ETH 5m` или `SOL 1m`\n\n"
            "**Доступные таймфреймы:**\n"
            "• 1m - 1 минута\n"
            "• 5m - 5 минут\n"
            "• 15m - 15 минут\n\n"
            "Для отмены введите /cancel",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_subscription'] = True
        
    elif query.data == "list_subs":
        await list_subscriptions(update, context)
        
    elif query.data == "back_to_menu":
        await back_to_menu(update, context)
    else:
        logger.warning("Unknown callback data: %s", query.data)
        # Возвращаем главное меню на всякий случай
        await back_to_menu(update, context)

async def handle_subscription_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода подписки"""
    logger.info("handle_subscription_input: text=%s user=%s", getattr(update.message, 'text', None), getattr(update.effective_user, 'id', None))
    text = update.message.text.strip()
    
    if text in ("📝 Подписаться", "📋 Активные подписки"):
        if text == "📝 Подписаться":
            fake_update = Update(update.update_id, message=update.message)
            class FakeQuery:
                def __init__(self, message):
                    self.data = "subscribe"
                    self.message = message
                async def answer(self):
                    return
                async def edit_message_text(self, *args, **kwargs):
                    await update.message.reply_text(*args, **kwargs)
            fake_update.callback_query = FakeQuery(update.message)
            await button_handler(fake_update, context)
            return
        if text == "📋 Активные подписки":
            await list_subscriptions(update, context)
            return
    
    if not context.user_data.get('awaiting_subscription'):
        return
    if text.lower() == '/cancel':
        context.user_data['awaiting_subscription'] = False
        await start(update, context)
        return
    
    # Парсим ввод
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ **Неверный формат!**\n\n"
            "Используйте: `ТОКЕН ТАЙМФРЕЙМ`\n"
            "Например: `ETH 5m` или `SOL 1m`\n\n"
            "Для отмены введите /cancel",
            parse_mode='Markdown'
        )
        return
    
    token = parts[0].upper()
    timeframe = parts[1].lower()
    
    # Валидация таймфрейма
    valid_timeframes = ['1m', '5m', '15m']
    if timeframe not in valid_timeframes:
        await update.message.reply_text(
            f"❌ **Неподдерживаемый таймфрейм!**\n\n"
            f"Доступны: {', '.join(valid_timeframes)}\n\n"
            f"Для отмены введите /cancel",
            parse_mode='Markdown'
        )
        return
    
    # Валидация токена (базовая)
    if not token.replace('.', '').isalnum():
        await update.message.reply_text(
            "❌ **Неверный формат токена!**\n\n"
            "Используйте стандартные тикеры: ETH, BTC, SOL и т.д.\n\n"
            "Для отмены введите /cancel",
            parse_mode='Markdown'
        )
        return
    
    # Сохраняем подписку
    try:
        redis_service = context.bot_data['redis_service']
        await redis_service.add_subscription(update.effective_user.id, token, timeframe)
        
        # Запускаем обработку если нужно
        bot_instance = context.bot_data['bot_instance']
        key = f"{token}_{timeframe}"
        
        if key not in bot_instance.active_tasks:
            # Создаем новую задачу для обработки токена
            task = asyncio.create_task(
                bot_instance.process_token(token, timeframe)
            )
            bot_instance.active_tasks[key] = task
            logger.info(f"Started processor for {key}")
        
        await update.message.reply_text(
            f"✅ **Подписка успешно добавлена!**\n\n"
            f"**Токен:** {token}\n"
            f"**Таймфрейм:** {timeframe}\n\n"
            f"Бот начнет анализировать рынок и присылать сигналы при их обнаружении.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error adding subscription: {e}")
        await update.message.reply_text(
            "❌ **Ошибка при добавлении подписки**\n\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode='Markdown'
        )
    
    finally:
        context.user_data['awaiting_subscription'] = False

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cancel"""
    logger.info("cancel handler: user=%s", getattr(update.effective_user, 'id', None))
    if context.user_data.get('awaiting_subscription'):
        context.user_data['awaiting_subscription'] = False
        await update.message.reply_text("❌ Ввод отменен.")
    
    await start(update, context)
