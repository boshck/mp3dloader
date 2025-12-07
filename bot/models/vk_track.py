"""
Модель для VK треков
"""

from dataclasses import dataclass
from typing import Optional
import os

@dataclass
class VKTrack:
    """Модель трека с VK"""
    
    id: int
    owner_id: int
    title: str
    artist: str
    duration: int  # в секундах
    url: str
    date: int = 0  # Unix timestamp даты добавления
    source: str = "vk"
    
    def __post_init__(self):
        """Валидация данных после инициализации"""
        if not self.title:
            self.title = "Unknown Title"
        if not self.artist:
            self.artist = "Unknown Artist"
    
    @property
    def formatted_duration(self) -> str:
        """Форматированная длительность в MM:SS"""
        if self.duration <= 0:
            return "0:00"
        
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes}:{seconds:02d}"
    
    @property
    def duration_str(self) -> str:
        """Возвращает длительность в формате MM:SS (для совместимости)"""
        return self.formatted_duration
    
    @property
    def full_title(self) -> str:
        """Полное название трека с исполнителем"""
        return f"{self.artist} - {self.title}"
    
    @property
    def display_name(self) -> str:
        """Название для отображения в интерфейсе"""
        # Обрезаем название до 25 символов и исполнителя до 15
        truncated_title = self.title[:22] + "..." if len(self.title) > 25 else self.title
        truncated_artist = self.artist[:12] + "..." if len(self.artist) > 15 else self.artist
        return f"🎵 {truncated_artist} - {truncated_title}"
    
    @property
    def info_text(self) -> str:
        """Информация о треке для отображения"""
        return f"⏱️ {self.formatted_duration} | 🎵 VK"
    
    @property
    def is_valid(self) -> bool:
        """Проверка валидности трека"""
        return (
            self.id and 
            self.owner_id and
            self.title and 
            self.artist and 
            self.duration > 0 and
            self.duration <= 3600 and  # Максимум 1 час
            self.url
        )
    
    def get_safe_filename(self) -> str:
        """Возвращает безопасное имя файла для сохранения"""
        import re

        def _clean(text: str, max_len: int) -> str:
            text = (text or "Unknown").replace('/', '-').replace('\\', '-')
            text = re.sub(r'[<>:"|?*]', '', text).strip()
            return text[:max_len] if len(text) > max_len else text
        
        safe_artist = _clean(self.artist, 40)
        safe_title = _clean(self.title, 60)
        
        if not safe_artist and not safe_title:
            return f"{self.id}.mp3"
        
        return f"{safe_artist} - {safe_title}.mp3"
    
    def to_dict(self) -> dict:
        """Конвертирует трек в словарь"""
        return {
            'id': self.id,
            'owner_id': self.owner_id,
            'title': self.title,
            'artist': self.artist,
            'duration': self.duration,
            'url': self.url,
            'source': self.source
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'VKTrack':
        """Создает трек из словаря"""
        return cls(
            id=data.get('id', 0),
            owner_id=data.get('owner_id', 0),
            title=data.get('title', ''),
            artist=data.get('artist', ''),
            duration=data.get('duration', 0),
            url=data.get('url', ''),
            source=data.get('source', 'vk')
        )
    
    @classmethod
    def from_vk_api_response(cls, track_data: dict) -> 'VKTrack':
        """Создает трек из ответа VK API"""
        return cls(
            id=track_data.get('id', 0),
            owner_id=track_data.get('owner_id', 0),
            title=track_data.get('title', ''),
            artist=track_data.get('artist', ''),
            duration=track_data.get('duration', 0),
            url=track_data.get('url', ''),
            date=track_data.get('date', 0),
            source='vk'
        )
    
    def __str__(self) -> str:
        return f"VKTrack(id={self.id}, title='{self.title}', artist='{self.artist}')"
    
    def __repr__(self) -> str:
        return self.__str__()
