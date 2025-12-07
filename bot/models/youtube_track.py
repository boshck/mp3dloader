"""
Модель трека YouTube
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class YouTubeTrack:
    """Модель трека YouTube"""
    
    def __init__(
        self,
        id: str,
        title: str,
        channel: str,
        duration: int,
        webpage_url: str,
        filesize: Optional[int] = None,
        filesize_approx: Optional[int] = None
    ):
        self.id = id
        self.title = title
        self.channel = channel  # artist equivalent
        self.duration = duration  # seconds
        self.webpage_url = webpage_url
        self.filesize = filesize or filesize_approx
        
    @property
    def artist(self) -> str:
        """Алиас для channel (для совместимости с другими типами треков)"""
        return self.channel
    
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
        return f"{self.title} - {self.channel}"
    
    @property
    def info_text(self) -> str:
        """Дополнительная информация для UI"""
        info_parts = [f"⏱ {self.formatted_duration}"]
        
        if self.filesize:
            size_mb = self.filesize / (1024 * 1024)
            info_parts.append(f"📦 {size_mb:.1f} МБ")
        
        info_parts.append("🎬 YouTube")
        
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
        
        safe_artist = _clean(self.channel, 40)
        safe_title = _clean(self.title, 60)
        
        # Если оба поля пустые после очистки — подстрахуемся id
        if not safe_artist and not safe_title:
            return f"{self.id}.mp3"
        
        return f"{safe_artist} - {safe_title}.mp3"
    
    @classmethod
    def from_yt_dlp_info(cls, info: dict) -> Optional['YouTubeTrack']:
        """
        Создать YouTubeTrack из ответа yt-dlp
        
        Args:
            info: Словарь с метаданными от yt-dlp
            
        Returns:
            YouTubeTrack или None если не удалось распарсить
        """
        try:
            video_id = info.get('id', '')
            return cls(
                id=video_id,
                title=info.get('title', 'Unknown'),
                channel=info.get('channel', info.get('uploader', 'Unknown')),
                duration=int(info.get('duration', 0)),
                webpage_url=info.get('webpage_url') or f"https://www.youtube.com/watch?v={video_id}",
                filesize=info.get('filesize'),
                filesize_approx=info.get('filesize_approx')
            )
        except Exception as e:
            logger.error(f"Error creating YouTubeTrack from yt-dlp info: {e}")
            return None

