"""
Reply клавиатуры для AMusic Bot
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def create_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Создает главное меню бота"""
    builder = ReplyKeyboardBuilder()
    
    builder.add(KeyboardButton(text="🎵 Поиск музыки"))
    builder.add(KeyboardButton(text="⭐ Избранное"))
    builder.add(KeyboardButton(text="📊 Статистика"))
    builder.add(KeyboardButton(text="⚙️ Настройки"))
    
    builder.adjust(2)  # 2 кнопки в ряду
    return builder.as_markup(resize_keyboard=True)


def create_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Создает клавиатуру с кнопкой отмены"""
    keyboard = [
        [KeyboardButton(text="❌ Отмена")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )
