"""
Модель трека SoundCloud
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SoundCloudTrack:
    """Модель трека SoundCloud"""
    
    def __init__(
        self,
        id: str,
        title: str,
        artist: str,
        duration: int,
        permalink_url: str,
        filesize: Optional[int] = None,
        filesize_approx: Optional[int] = None
    ):
        self.id = id
        self.title = title
        self.artist = artist
        self.duration = duration  # seconds
        self.permalink_url = permalink_url
        self.filesize = filesize or filesize_approx
    
    @property
    def is_valid(self) -> bool:
        """
        Проверка валидности трека
        - Длительность: 20-3600 сек (20 сек - 60 минут)
        - Размер: ≤100 МБ (если известен)
        """
        # Проверка длительности
        if self.duration < 20 or self.duration > 3600:
            return False
        
        # Проверка размера файла
        if self.filesize:
            max_size_bytes = 100 * 1024 * 1024  # 100 МБ
            if self.filesize > max_size_bytes:
                return False
        
        return True
    
    @property
    def display_name(self) -> str:
        """Форматированное название для UI"""
        return f"{self.title} - {self.artist}"
    
    @property
    def info_text(self) -> str:
        """Дополнительная информация для UI"""
        info_parts = [f"⏱ {self.formatted_duration}"]
        
        if self.filesize:
            size_mb = self.filesize / (1024 * 1024)
            info_parts.append(f"📦 {size_mb:.1f} МБ")
        
        info_parts.append("🎧 SoundCloud")
        
        return " | ".join(info_parts)
    
    @property
    def formatted_duration(self) -> str:
        """Длительность в формате M:SS или H:MM:SS"""
        if self.duration < 0:
            return "0:00"
        
        hours = self.duration // 3600
        minutes = (self.duration % 3600) // 60
        seconds = self.duration % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    def get_safe_filename(self) -> str:
        """
        Безопасное имя файла для сохранения
        
        Returns:
            Имя файла в формате: Artist - Title.mp3 (c очисткой)
        """
        def _clean(text: str, max_len: int) -> str:
            text = text or "Unknown"
            text = re.sub(r'[<>:"/\\|?*]', '', text).strip()
            return text[:max_len] if len(text) > max_len else text
        
        safe_artist = _clean(self.artist, 40)
        safe_title = _clean(self.title, 60)
        
        if not safe_artist and not safe_title:
            return f"{self.id}.mp3"
        
        return f"{safe_artist} - {safe_title}.mp3"
    
    @classmethod
    def from_yt_dlp_info(cls, info: dict) -> Optional['SoundCloudTrack']:
        """
        Создать SoundCloudTrack из ответа yt-dlp
        
        Args:
            info: Словарь с метаданными от yt-dlp
            
        Returns:
            SoundCloudTrack или None если не удалось распарсить
        """
        try:
            track_id = info.get('id', '')
            return cls(
                id=track_id,
                title=info.get('title', 'Unknown'),
                artist=info.get('uploader', info.get('artist', 'Unknown')),
                duration=int(info.get('duration', 0)),
                permalink_url=info.get('webpage_url') or info.get('url') or f"https://soundcloud.com/{track_id}",
                filesize=info.get('filesize'),
                filesize_approx=info.get('filesize_approx')
            )
        except Exception as e:
            logger.error(f"Error creating SoundCloudTrack from yt-dlp info: {e}")
            return None

