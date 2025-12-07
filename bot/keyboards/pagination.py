"""
Клавиатуры пагинации для AMusic Bot
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional


def create_pagination_keyboard(
    current_page: int, 
    total_pages: int, 
    callback_prefix: str = "page"
) -> InlineKeyboardMarkup:
    """Создает клавиатуру пагинации"""
    if total_pages <= 1:
        return InlineKeyboardMarkup(inline_keyboard=[])
    
    keyboard = []
    pagination_row = []
    
    # Кнопка "Назад"
    if current_page > 1:
        pagination_row.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"{callback_prefix}_{current_page-1}"
            )
        )
    else:
        # Циклическая навигация - с первой страницы на последнюю
        pagination_row.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"{callback_prefix}_{total_pages}"
            )
        )
    
    # Номер страницы
    pagination_row.append(
        InlineKeyboardButton(
            text=f"{current_page}/{total_pages}",
            callback_data="current_page"
        )
    )
    
    # Кнопка "Вперед"
    if current_page < total_pages:
        pagination_row.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"{callback_prefix}_{current_page+1}"
            )
        )
    else:
        # Циклическая навигация - с последней страницы на первую
        pagination_row.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"{callback_prefix}_1"
            )
        )
    
    keyboard.append(pagination_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_search_results_keyboard(
    tracks: List[dict],
    current_page: int = 1,
    total_pages: int = 1,
    callback_prefix: str = "search"
) -> InlineKeyboardMarkup:
    """Создает клавиатуру с результатами поиска и пагинацией"""
    keyboard = []
    
    # Кнопки для каждого трека
    for i, track in enumerate(tracks, 1):
        track_id = track.get('id', f'track_{i}')
        title = track.get('title', f'Трек {i}')[:30] + "..." if len(track.get('title', '')) > 30 else track.get('title', f'Трек {i}')
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{i}. {title}",
                callback_data=f"select_{track_id}"
            )
        ])
    
    # Добавляем пагинацию
    if total_pages > 1:
        pagination_keyboard = create_pagination_keyboard(
            current_page, 
            total_pages, 
            callback_prefix
        )
        keyboard.extend(pagination_keyboard.inline_keyboard)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
