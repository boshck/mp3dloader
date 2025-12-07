"""
DEPRECATED: Обработчик команды /start
ВСЕ КОМАНДЫ ПЕРЕНЕСЕНЫ В music_search.py
Этот файл больше не используется!
"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from bot.utils.logger import get_logger

logger = get_logger()
router = Router()


@router.message(CommandStart())
async def start_command(message: Message):
    """Обработчик команды /start"""
    try:
        user = message.from_user
        logger.info(f"User {user.id} ({user.username}) started the bot")
        
        welcome_text = f"""
🎵 <b>Добро пожаловать в AMusic Bot!</b>

Привет, {user.first_name}! 👋

Я помогу тебе найти и скачать музыку с YouTube в высоком качестве!

<b>🚀 Что я умею:</b>
• 🔍 Ищу музыку по названию песни или исполнителю
• 🎵 Скачиваю аудио в качестве до 320kbps
• 📱 Показываю результаты с удобными кнопками
• ⚡ Быстро нахожу нужные треки

<b>💡 Как пользоваться:</b>
Просто отправь название песни или исполнителя, например:
• "Imagine Dragons - Thunder"
• "The Weeknd - Blinding Lights" 
• "Queen - Bohemian Rhapsody"

<b>📊 Лимиты:</b>
• Бесплатно: 5 запросов/час, 30/день
• 💎 Премиум: 50 запросов/час, 500/день

<b>🎯 Команды:</b>
/help - подробная справка
/start - начать заново

Начни поиск прямо сейчас! 🎶
        """
        
        await message.answer(welcome_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await message.answer(
            "Произошла ошибка. Попробуйте позже.",
            parse_mode="HTML"
        )
