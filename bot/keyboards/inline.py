"""
Inline клавиатуры для AMusic Bot
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional


def create_track_keyboard(track_id: str, page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора трека"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🎵 Скачать",
                callback_data=f"download_{track_id}"
            ),
            InlineKeyboardButton(
                text="⭐ Избранное",
                callback_data=f"favorite_{track_id}"
            )
        ]
    ]
    
    # Добавляем пагинацию если нужно
    if total_pages > 1:
        pagination_row = []
        
        # Кнопка "Назад"
        if page > 1:
            pagination_row.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"page_{page-1}"
                )
            )
        else:
            pagination_row.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"page_{total_pages}"
                )
            )
        
        # Номер страницы
        pagination_row.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data="current_page"
            )
        )
        
        # Кнопка "Вперед"
        if page < total_pages:
            pagination_row.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"page_{page+1}"
                )
            )
        else:
            pagination_row.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data="page_1"
                )
            )
        
        keyboard.append(pagination_row)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_source_selection_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора источника музыки"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🎵 YouTube",
                callback_data="source_youtube"
            ),
            InlineKeyboardButton(
                text="🎧 SoundCloud",
                callback_data="source_soundcloud"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_settings_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру настроек"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🖼️ Картинка поста",
                callback_data="toggle_image"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data="show_stats"
            ),
            InlineKeyboardButton(
                text="⭐ Избранное",
                callback_data="show_favorites"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
