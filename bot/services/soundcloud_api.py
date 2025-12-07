"""
Сервис для работы с SoundCloud через yt-dlp
"""
import os
import logging
import asyncio
from typing import List, Optional
from pathlib import Path
import yt_dlp

from bot.models.soundcloud_track import SoundCloudTrack

logger = logging.getLogger(__name__)


class SoundCloudAPI:
    """Сервис для работы с SoundCloud через yt-dlp"""
    
    def __init__(self):
        """Инициализация SoundCloud API сервиса"""
        self.temp_dir = Path("assets/temp")
        self.temp_dir.mkdir(exist_ok=True)
        
        # Базовые опции yt-dlp
        self.base_ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 30,
            'retries': 3
        }
        
        logger.info("SoundCloud API service initialized (via yt-dlp)")
    
    async def extract_track_info(self, url: str) -> Optional[SoundCloudTrack]:
        """
        Получить метаданные трека через yt-dlp (download=False)
        
        Args:
            url: URL SoundCloud трека
            
        Returns:
            SoundCloudTrack или None
        """
        try:
            logger.debug(f"Extracting SoundCloud track info: {url}")
            
            ydl_opts = {
                **self.base_ydl_opts,
                'format': 'bestaudio/best',
            }
            
            def _blocking_extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            # Выполняем в отдельном потоке
            info = await asyncio.to_thread(_blocking_extract)
            
            if not info:
                return None
            
            track = SoundCloudTrack.from_yt_dlp_info(info)
            
            if track:
                logger.info(f"SoundCloud track extracted: {track.title} by {track.artist}")
            
            return track
            
        except Exception as e:
            logger.error(f"Error extracting SoundCloud track info: {e}")
            return None
    
    async def extract_playlist_info(self, url: str, max_tracks: int = 50) -> List[SoundCloudTrack]:
        """
        Получить список треков из плейлиста (download=False, первые 50 валидных)
        
        Args:
            url: URL SoundCloud плейлиста
            max_tracks: Максимальное количество валидных треков
            
        Returns:
            Список SoundCloudTrack (до max_tracks валидных треков)
        """
        try:
            logger.info(f"Extracting SoundCloud playlist: {url}")
            
            ydl_opts = {
                **self.base_ydl_opts,
                'extract_flat': 'in_playlist',  # Только метаданные плейлиста
            }
            
            def _blocking_extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            # Выполняем в отдельном потоке
            playlist_info = await asyncio.to_thread(_blocking_extract)
            
            if not playlist_info or 'entries' not in playlist_info:
                logger.warning("No entries found in SoundCloud playlist")
                return []
            
            tracks = []
            entries = playlist_info['entries']
            
            logger.info(f"Found {len(entries)} entries in playlist, extracting details...")
            
            # Извлекаем детальную информацию для каждого трека
            for entry in entries:
                if len(tracks) >= max_tracks:
                    break
                
                if not entry:
                    continue
                
                try:
                    # Если это только ID - нужно получить полную информацию
                    if 'duration' not in entry:
                        track_url = entry.get('url') or entry.get('webpage_url')
                        if track_url:
                            track = await self.extract_track_info(track_url)
                    else:
                        # Уже есть вся информация
                        track = SoundCloudTrack.from_yt_dlp_info(entry)
                    
                    if track and track.is_valid:
                        tracks.append(track)
                        logger.debug(f"Added valid track: {track.title}")
                    else:
                        logger.debug(f"Skipped invalid track: {entry.get('title', 'Unknown')}")
                
                except Exception as e:
                    logger.warning(f"Error processing playlist entry: {e}")
                    continue
            
            logger.info(f"Extracted {len(tracks)} valid tracks from SoundCloud playlist")
            return tracks
            
        except Exception as e:
            logger.error(f"Error extracting SoundCloud playlist: {e}")
            return []
    
    async def download_audio(self, track: SoundCloudTrack) -> Optional[str]:
        """
        Скачать аудио через yt-dlp
        
        Args:
            track: Объект SoundCloudTrack
            
        Returns:
            Путь к скачанному файлу или None
        """
        try:
            logger.info(f"⬇️ Downloading SoundCloud track: '{track.title}' by '{track.artist}'")
            
            # Создаем имя файла
            filename = track.get_safe_filename()
            file_path = self.temp_dir / filename
            
            # Проверяем кеш
            if file_path.exists():
                logger.debug(f"File already exists in cache: {file_path}")
                return str(file_path)
            
            # Опции для скачивания
            ydl_opts = {
                **self.base_ydl_opts,
                'format': 'bestaudio/best',
                'outtmpl': str(file_path.with_suffix('')),  # без расширения
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '256',
                }],
            }
            
            def _blocking_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([track.permalink_url])
            
            # Скачиваем в отдельном потоке
            await asyncio.to_thread(_blocking_download)
            
            # Проверяем результат
            if file_path.exists():
                logger.info(f"✅ SoundCloud download completed: {filename}")
                return str(file_path)
            else:
                logger.error(f"File not found after download: {file_path}")
                return None
            
        except Exception as e:
            logger.error(f"SoundCloud download error: {e}")
            return None

