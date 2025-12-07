"""
DEPRECATED: Обработчик команды /help
ВСЕ КОМАНДЫ ПЕРЕНЕСЕНЫ В music_search.py
Этот файл больше не используется!
"""
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from bot.utils.logger import get_logger

logger = get_logger()
router = Router()


@router.message(Command("help"))
async def help_command(message: Message):
    """Обработчик команды /help"""
    try:
        help_text = """
🎵 <b>AMusic Bot - Подробная справка</b>

<b>🚀 Возможности бота:</b>
• 🔍 Поиск музыки через YouTube API
• 🎵 Скачивание в качестве до 320kbps MP3
• 📱 Удобные inline клавиатуры для выбора
• ⚡ Быстрый поиск и кеширование треков
• 🖼️ Красивое отображение результатов

<b>📋 Как пользоваться:</b>
1️⃣ Отправь название песни или исполнителя
2️⃣ Выбери нужный трек из списка (10 на страницу)
3️⃣ Нажми "🎵 Скачать" для загрузки
4️⃣ Получи MP3 файл высокого качества

<b>💡 Примеры запросов:</b>
• "Imagine Dragons - Thunder"
• "The Weeknd - Blinding Lights"
• "Queen - Bohemian Rhapsody"
• "Ed Sheeran - Shape of You"
• "Billie Eilish"

<b>⚙️ Технические характеристики:</b>
• Формат: MP3
• Качество: до 320kbps
• Размер: до 50MB (лимит Telegram)
• Длительность: до 1 часа

<b>📊 Лимиты использования:</b>
<b>Бесплатно:</b>
• 5 запросов в час
• 30 запросов в день

<b>💎 Премиум (100₽/месяц):</b>
• 50 запросов в час
• 500 запросов в день
• Приоритетная поддержка

<b>🎯 Команды:</b>
/start - начать работу с ботом
/help - показать эту справку

<b>❓ Решение проблем:</b>
• Не находит трек? Попробуй английское название
• Ошибка скачивания? Выбери другой трек
• Превышен лимит? Подожди час или оформи премиум

<b>🔧 Поддержка:</b>
Если что-то не работает, попробуй:
• Переформулировать запрос
• Использовать более точное название
• Проверить интернет-соединение

Удачного поиска музыки! 🎶✨
        """
        
        await message.answer(help_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await message.answer(
            "Произошла ошибка. Попробуйте позже.",
            parse_mode="HTML"
        )
