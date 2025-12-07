"""
Сервис для работы с VK API
"""

import os
import logging
import asyncio

logger = logging.getLogger(__name__)
import subprocess
import json
import ssl
from typing import List, Optional, Dict, Any
from pathlib import Path
import aiohttp
import yt_dlp

from bot.models.vk_track import VKTrack

logger = logging.getLogger(__name__)


class VKTokenExpiredError(Exception):
    """Исключение, выбрасываемое когда VK токен истёк (error_code 5)"""
    
    def __init__(self, token_preview: str, full_token: str, error_details: dict):
        """
        Args:
            token_preview: Первые 15 символов токена для идентификации
            full_token: Полный токен (для внутренней обработки)
            error_details: Детали ошибки от VK API
        """
        self.token_preview = token_preview
        self.full_token = full_token
        self.error_details = error_details
        super().__init__(f"VK token expired: {token_preview}")


class VKAPI:
    """Сервис для работы с VK API"""
    
    def __init__(self, tokens: list = None):
        """
        Инициализация VK API сервиса
        
        Args:
            tokens: Список VK токенов (если None, берётся из переменной окружения)
        """
        if tokens:
            self.tokens = tokens
        else:
            # Fallback на старое поведение
            token = os.getenv("VK_TOKEN")
            if not token:
                raise ValueError("VK_TOKEN not found in environment variables")
            self.tokens = [token]
        
        if not self.tokens:
            raise ValueError("No VK tokens provided")
        
        self.api_version = '5.131'
        self.base_url = "https://api.vk.com/method"
        
        # Настройки для yt-dlp
        self.temp_dir = Path("assets/temp")
        self.temp_dir.mkdir(exist_ok=True)
        
        # Множество битых токенов (исключаются из ротации)
        self.dead_tokens = set()
        
        logger.info(f"VK API service initialized with {len(self.tokens)} token(s)")
    
    def _get_random_token(self) -> tuple:
        """
        Получить случайный токен из списка (исключая битые)
        
        Returns:
            (token, token_preview): полный токен и его превью для логов
            
        Raises:
            ValueError: Если все токены битые
        """
        import random
        
        # Фильтруем живые токены
        alive_tokens = [t for t in self.tokens if t not in self.dead_tokens]
        
        if not alive_tokens:
            logger.error(f"All {len(self.tokens)} VK tokens are dead!")
            raise ValueError("No working VK tokens available")
        
        token = random.choice(alive_tokens)
        # Показываем первые 15 символов для идентификации
        token_preview = f"{token[:15]}..." if len(token) > 15 else token
        
        if len(self.dead_tokens) > 0:
            logger.debug(f"Active tokens: {len(alive_tokens)}/{len(self.tokens)} (dead: {len(self.dead_tokens)})")
        
        return token, token_preview
    
    def _parse_audio_string(self, audio_str: str) -> Optional[Dict[str, Any]]:
        """
        Парсит строку вида "owner_id_audio_id" или "owner_id_audio_id_access_key"
        
        Args:
            audio_str: Строка с ID трека
            
        Returns:
            Dict с owner_id, audio_id, access_key или None
        """
        try:
            parts = audio_str.split('_')
            if len(parts) >= 2:
                return {
                    'owner_id': int(parts[0]),
                    'audio_id': int(parts[1]),
                    'access_key': parts[2] if len(parts) > 2 else None
                }
            return None
        except (ValueError, IndexError):
            logger.warning(f"Failed to parse audio string: {audio_str}")
            return None
    
    async def search_tracks(self, query: str, max_results: int = 10) -> List[VKTrack]:
        """
        Поиск треков через VK API с автоматическим retry при истечении токена
        
        Args:
            query: Поисковый запрос
            max_results: Максимальное количество результатов
            
        Returns:
            Список найденных треков
        """
        # Retry механизм: пытаемся использовать все доступные токены
        max_retries = len(self.tokens)
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Используем случайный токен для балансировки нагрузки
                token, token_preview = self._get_random_token()
                logger.info(f"🔍 Searching VK for '{query}' with limit {max_results} (Token: {token_preview}, attempt {attempt + 1}/{max_retries})")
                
                params = {
                    'access_token': token,
                    'v': self.api_version,
                    'q': query,
                    'count': max_results,
                    'offset': 0
                }
                
                logger.debug(f"VK API request URL: {self.base_url}/audio.search")
            
                # Мягкий SSL контекст для VK API (компромисс безопасности)
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False  # Не проверяем имя хоста
                ssl_context.verify_mode = ssl.CERT_NONE  # Не проверяем сертификаты
                connector = aiohttp.TCPConnector(ssl=ssl_context)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(f"{self.base_url}/audio.search", params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            logger.debug(f"VK API response status: {response.status}")
                            
                            # Проверяем есть ли ошибки в ответе
                            if 'error' in data:
                                error_code = data['error'].get('error_code')
                                error_msg = data['error'].get('error_msg', 'Unknown error')
                                logger.error(f"VK API Error: {error_code} - {error_msg}")
                                
                                # Если токен истёк (error_code 5) - помечаем как битый и пытаемся другой
                                if error_code == 5:
                                    self.dead_tokens.add(token)
                                    logger.critical(f"VK token expired and marked as dead: {token_preview}")
                                    logger.warning(f"Active tokens remaining: {len(self.tokens) - len(self.dead_tokens)}/{len(self.tokens)}")
                                    
                                    # Сохраняем ошибку для возможного выброса
                                    last_error = VKTokenExpiredError(
                                        token_preview=token_preview,
                                        full_token=token,
                                        error_details=data['error']
                                    )
                                    
                                    # Если есть ещё живые токены - пробуем следующий
                                    if len(self.dead_tokens) < len(self.tokens):
                                        logger.info(f"🔄 Retrying with another token...")
                                        continue
                                    else:
                                        # Все токены мёртвые - выбрасываем исключение
                                        logger.critical("❌ All VK tokens are dead!")
                                        raise last_error
                                        
                                return []
                            
                            items = data.get('response', {}).get('items', [])
                            logger.info(f"📊 VK API returned {len(items)} tracks")
                            
                            if not data.get('response'):
                                logger.warning("VK API: No 'response' field in answer")
                            elif 'items' not in data.get('response', {}):
                                logger.warning("VK API: No 'items' field in response")
                            
                            tracks = []
                            for item in items:
                                try:
                                    track = VKTrack.from_vk_api_response(item)
                                    if track.is_valid:
                                        tracks.append(track)
                                        logger.debug(f"Added track: '{track.title}' by '{track.artist}'")
                                    else:
                                        logger.debug(f"Invalid track skipped: {item.get('title', 'Unknown')}")
                                except Exception as e:
                                    logger.warning(f"Error parsing track: {e}")
                                    continue
                            
                            logger.info(f"✅ VK search completed: {len(tracks)} valid tracks from {len(items)} results")
                            return tracks
                        else:
                            logger.error(f"VK API error: {response.status}")
                            return []
                            
            except VKTokenExpiredError:
                # Уже обработано выше, пробуем следующий токен
                continue
            except ValueError as e:
                # Все токены мёртвые (_get_random_token выбросил ValueError)
                logger.critical(f"No working tokens available: {e}")
                if last_error:
                    raise last_error
                raise
            except Exception as e:
                logger.error(f"VK search error on attempt {attempt + 1}: {e}")
                last_error = e
                # Продолжаем пробовать другие токены при других ошибках
                if attempt < max_retries - 1:
                    continue
        
        # Если все попытки исчерпаны
        logger.error(f"VK search failed after {max_retries} attempts")
        if last_error and isinstance(last_error, VKTokenExpiredError):
            raise last_error
        return []
    
    async def get_audio_url(self, owner_id: int, audio_id: int) -> Optional[str]:
        """
        Получение URL аудиофайла через VK API
        
        Args:
            owner_id: ID владельца трека
            audio_id: ID трека
            
        Returns:
            URL аудиофайла или None
        """
        try:
            # Используем случайный токен для балансировки нагрузки
            token, token_preview = self._get_random_token()
            logger.debug(f"Getting audio URL for {owner_id}_{audio_id} (Token: {token_preview})")
            
            params = {
                'access_token': token,
                'v': self.api_version,
                'audios': f"{owner_id}_{audio_id}"
            }
            
            # Мягкий SSL контекст для VK API (компромисс безопасности)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False  # Не проверяем имя хоста
            ssl_context.verify_mode = ssl.CERT_NONE  # Не проверяем сертификаты
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{self.base_url}/audio.getById", params=params) as response:
                    logger.debug(f"VK API getById response status: {response.status}")
                    
                    if response.status == 200:
                        response_json = await response.json()
                        
                        if response_json.get('response'):
                            audio_data = response_json['response'][0]
                            url = audio_data.get('url')
                            logger.debug(f"Audio URL obtained: {url[:50] if url else 'None'}...")
                            return url
                        else:
                            logger.warning("VK API: No data in response")
                            return None
                    else:
                        logger.error(f"VK API: Error getting URL: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting audio URL: {e}")
            return None
    
    async def get_track_by_url(self, owner_id: int, audio_id: int, access_key: Optional[str] = None) -> Optional[VKTrack]:
        """
        Получение трека по owner_id и audio_id
        
        Args:
            owner_id: ID владельца трека
            audio_id: ID трека
            access_key: Ключ доступа (опционально)
            
        Returns:
            VKTrack или None
        """
        try:
            # Используем случайный токен для балансировки нагрузки
            token, token_preview = self._get_random_token()
            logger.debug(f"Getting track by URL: {owner_id}_{audio_id} (Token: {token_preview})")
            
            # Формируем ID трека
            audio_identifier = f"{owner_id}_{audio_id}"
            if access_key:
                audio_identifier += f"_{access_key}"
            
            params = {
                'access_token': token,
                'v': self.api_version,
                'audios': audio_identifier
            }
            
            # Мягкий SSL контекст для VK API
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{self.base_url}/audio.getById", params=params) as response:
                    logger.debug(f"VK API getById response status: {response.status}")
                    
                    if response.status == 200:
                        response_json = await response.json()
                        
                        if 'error' in response_json:
                            error_code = response_json['error'].get('error_code')
                            error_msg = response_json['error'].get('error_msg', 'Unknown error')
                            logger.warning(f"VK API Error: {error_code} - {error_msg}")
                            
                            # Если токен истёк (error_code 5) - выбрасываем исключение
                            if error_code == 5:
                                self.dead_tokens.add(token)
                                logger.critical(f"VK token expired and marked as dead: {token_preview}")
                                logger.warning(f"Active tokens remaining: {len(self.tokens) - len(self.dead_tokens)}/{len(self.tokens)}")
                                raise VKTokenExpiredError(
                                    token_preview=token_preview,
                                    full_token=token,
                                    error_details=response_json['error']
                                )
                            return None
                        
                        if response_json.get('response') and len(response_json['response']) > 0:
                            audio_data = response_json['response'][0]
                            track = VKTrack.from_vk_api_response(audio_data)
                            
                            if track and track.is_valid:
                                logger.info(f"✅ Got VK track: '{track.title}' by '{track.artist}'")
                                return track
                            else:
                                logger.warning("Track is invalid or unavailable")
                                return None
                        else:
                            logger.warning("VK API: No data in response (track may be private or deleted)")
                            return None
                    else:
                        logger.error(f"VK API: Error getting track: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting track by URL: {e}")
            return None
    
    async def download_audio(self, track: VKTrack) -> Optional[str]:
        """
        Скачивание аудио с VK
        
        Args:
            track: Объект трека VK
            
        Returns:
            Путь к скачанному файлу или None
        """
        try:
            logger.info(f"⬇️ Downloading VK track: '{track.title}' by '{track.artist}'")
            
            # Проверки безопасности
            from bot.config.settings import settings
            
            # Проверка длительности трека (если доступна)
            if hasattr(track, 'duration') and track.duration:
                duration_minutes = track.duration / 60
                if duration_minutes > settings.max_track_duration_minutes:
                    raise Exception(
                        f"⏱️ Трек слишком длинный: {duration_minutes:.1f} минут. "
                        f"Максимум: {settings.max_track_duration_minutes} минут. "
                        f"Прости, но не сможем его скачать 😔"
                    )
            
            # Создаем безопасное имя файла
            filename = track.get_safe_filename()
            file_path = self.temp_dir / filename
            
            # Проверяем, есть ли файл в кеше
            if file_path.exists():
                logger.debug(f"File already exists in cache: {file_path}")
                return str(file_path)
            
            logger.debug(f"Saving as: {file_path}")
            
            # Получаем URL аудиофайла
            audio_url = await self.get_audio_url(track.owner_id, track.id)
            if not audio_url:
                logger.error("Failed to get audio URL")
                return None
            
            logger.debug(f"Audio URL: {audio_url[:50]}...")
            
            # Проверяем, является ли URL M3U8 плейлистом
            if audio_url.endswith('.m3u8') or 'm3u8' in audio_url:
                logger.info("🎵 M3U8 stream detected, using yt-dlp")
                return await self._download_m3u8_to_mp3(audio_url, str(file_path))
            else:
                logger.info("🎵 Direct download via aiohttp")
                return await self._download_direct(audio_url, str(file_path))
                
        except Exception as e:
            logger.error(f"VK download error: {e}")
            return None
    
    async def _download_m3u8_to_mp3(self, m3u8_url: str, output_path: str) -> Optional[str]:
        """Скачивает M3U8 поток и конвертирует в MP3"""
        try:
            # Проверяем, существует ли уже файл
            if os.path.exists(output_path):
                logger.debug(f"File already exists in cache: {output_path}")
                return output_path
            
            logger.debug(f"yt-dlp downloading M3U8 stream from {m3u8_url[:50]}...")
            logger.debug(f"Output path: {output_path}")
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_path.replace('.mp3', '.%(ext)s'),
                'extractaudio': True,
                'audioformat': 'mp3',
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 30,     # 30 секунд на соединение
                'retries': 3,             # 3 попытки в yt-dlp
                'fragment_retries': 3,    # повторы для фрагментов
            }
            
            # Создаем вспомогательную функцию для блокирующей операции
            def _blocking_ytdlp_extract(m3u8_url, ydl_opts):
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(m3u8_url, download=True)
            
            # Выполняем блокирующую операцию в отдельном потоке с retry
            max_retries = 3
            retry_delay = 3  # Увеличиваем задержку до 3 секунд
            info = None
            
            for attempt in range(max_retries):
                try:
                    info = await asyncio.to_thread(_blocking_ytdlp_extract, m3u8_url, ydl_opts)
                    break  # Успешно
                except Exception as e:
                    if attempt < max_retries - 1:
                        error_str = str(e).lower()
                        if any(keyword in error_str for keyword in [
                            "ssl", "timeout", "connection", "handshake", 
                            "transporterror", "unable to download"
                        ]):
                            logger.warning(f"Network error on attempt {attempt + 1}/{max_retries}: {e}")
                            logger.info(f"Retrying in {retry_delay}s...")
                            await asyncio.sleep(retry_delay)
                        else:
                            raise  # Не сетевая ошибка, не повторяем
                    else:
                        logger.error(f"Failed after {max_retries} attempts: {e}")
                        raise
            
            if not info:
                raise Exception("Failed to extract info after all retries")
            
            logger.info(f"✅ yt-dlp download completed: {os.path.basename(output_path)}")
            logger.debug(f"Duration: {info.get('duration', 'N/A')}s, Size: {info.get('filesize', 'N/A')} bytes")
            
            # Проверяем и обрабатываем .part файлы
            if not os.path.exists(output_path):
                part_file = output_path + '.part'
                if os.path.exists(part_file):
                    # Попробовать переименовать .part файл
                    try:
                        os.rename(part_file, output_path)
                        logger.info(f"Renamed .part file to {output_path}")
                    except OSError as e:
                        logger.warning(f"Could not rename .part file: {e}")
                        # Подождать и попробовать снова
                        await asyncio.sleep(1)
                        try:
                            os.rename(part_file, output_path)
                            logger.debug("Renamed .part file after delay")
                        except:
                            pass
            
            # yt-dlp скачивает как .mp4, нужно переименовать в .mp3
            actual_file = output_path.replace('.mp3', '.mp4')
            if os.path.exists(actual_file):
                logger.debug(f"Renaming {actual_file} to {output_path}")
                os.rename(actual_file, output_path)
            else:
                logger.debug(f"Expected file not found: {actual_file}, searching for alternatives")
                # Попробуем найти файл с любым расширением
                base_name = output_path.replace('.mp3', '')
                for ext in ['.mp4', '.m4a', '.webm', '.opus']:
                    test_file = base_name + ext
                    if os.path.exists(test_file):
                        logger.debug(f"Found alternative file {test_file}, renaming to {output_path}")
                        os.rename(test_file, output_path)
                        break
            
            return output_path
                
        except Exception as e:
            logger.error(f"yt-dlp download error: {e}")
            return None
    
    async def _download_direct(self, audio_url: str, save_path: str) -> Optional[str]:
        """Прямое скачивание через aiohttp"""
        connector = None
        session = None
        try:
            logger.debug(f"Direct download from {audio_url[:50]}...")
            logger.debug(f"Save path: {save_path}")
            
            # Мягкий SSL контекст для VK API (компромисс безопасности)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False  # Не проверяем имя хоста
            ssl_context.verify_mode = ssl.CERT_NONE  # Не проверяем сертификаты
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            session = aiohttp.ClientSession(connector=connector)
            
            async with session.get(audio_url) as response:
                logger.debug(f"HTTP {response.status}, Content-Type: {response.headers.get('content-type')}")
                logger.debug(f"Content-Length: {response.headers.get('content-length')} bytes")
                
                # Проверка размера файла по Content-Length
                from bot.config.settings import settings
                content_length = response.headers.get('Content-Length')
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    if size_mb > settings.max_file_size_mb:
                        raise Exception(
                            f"📦 Файл слишком большой: {size_mb:.1f} МБ. "
                            f"Максимум: {settings.max_file_size_mb} МБ. "
                            f"Прости, но не сможем его скачать 😔"
                        )
                
                if response.status == 200:
                    # Скачивание с контролем размера
                    downloaded_size = 0
                    max_size_bytes = settings.max_file_size_mb * 1024 * 1024
                    
                    with open(save_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            if downloaded_size + len(chunk) > max_size_bytes:
                                raise Exception(
                                    f"📦 Превышен лимит размера файла: {settings.max_file_size_mb} МБ. "
                                    f"Прости, но не сможем его скачать 😔"
                                )
                            f.write(chunk)
                            downloaded_size += len(chunk)
                    
                    # Проверяем размер файла
                    file_size = os.path.getsize(save_path)
                    logger.info(f"✅ Audio saved successfully: {os.path.basename(save_path)}")
                    logger.debug(f"File size: {file_size} bytes")
                    return save_path
                else:
                    logger.error(f"Download failed: HTTP {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Direct download error: {str(e)}")
            return None
        finally:
            # Гарантируем закрытие сессии и коннектора
            if session:
                await session.close()
            if connector:
                await connector.close()
    
    async def get_audio_duration(self, file_path: str) -> int:
        """Получает длительность аудиофайла в секундах"""
        try:
            logger.debug(f"Getting audio duration: {file_path}")
            result = await asyncio.create_subprocess_exec(
                'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                '-show_format', file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await result.communicate()
            
            if result.returncode == 0:
                data = json.loads(stdout.decode())
                duration = float(data['format']['duration'])
                logger.debug(f"Audio duration: {duration}s")
                return int(duration)
            else:
                logger.error(f"ffprobe error: {stderr.decode()}")
                return 0
        except Exception as e:
            logger.error(f"Error getting audio duration: {e}")
            return 0

    
    async def get_playlist(self, owner_id: int, playlist_id: int, access_hash: Optional[str] = None) -> List[VKTrack]:
        """
        Получить плейлист через VK API
        
        Args:
            owner_id: ID владельца плейлиста
            playlist_id: ID плейлиста
            access_hash: Хеш доступа (если нужен)
            
        Returns:
            Список первых 50 валидных треков
        """
        try:
            # Используем случайный токен
            token, token_preview = self._get_random_token()
            logger.info(f"🎵 Getting VK playlist: {owner_id}_{playlist_id} (Token: {token_preview})")
            
            params = {
                'access_token': token,
                'v': self.api_version,
                'owner_id': owner_id,
                'playlist_id': playlist_id,
            }
            
            if access_hash:
                params['access_hash'] = access_hash
            
            # Мягкий SSL контекст
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{self.base_url}/audio.getPlaylistById", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if 'error' in data:
                            error_code = data['error'].get('error_code')
                            error_msg = data['error'].get('error_msg', 'Unknown error')
                            logger.error(f"VK API Error: {error_code} - {error_msg}")
                            
                            # Если токен истёк (error_code 5) - выбрасываем исключение
                            if error_code == 5:
                                self.dead_tokens.add(token)
                                logger.critical(f"VK token expired and marked as dead: {token_preview}")
                                logger.warning(f"Active tokens remaining: {len(self.tokens) - len(self.dead_tokens)}/{len(self.tokens)}")
                                raise VKTokenExpiredError(
                                    token_preview=token_preview,
                                    full_token=token,
                                    error_details=data['error']
                                )
                            return []
                        
                        # Получаем треки из плейлиста
                        response = data.get("response")
                        
                        # Проверяем тип ответа
                        if isinstance(response, dict):
                            # Словарь - проверяем наличие массивов треков
                            if "audios" in response and isinstance(response["audios"], list):
                                items = response["audios"]
                                logger.debug("Using response['audios']")
                            elif "items" in response and isinstance(response["items"], list):
                                items = response["items"]
                                logger.debug("Using response['items']")
                            else:
                                # Нет треков - пытаемся получить через audio.get (fallback)
                                logger.warning(f"No audio items in playlist response (keys: {list(response.keys())})")
                                logger.info("Trying fallback: audio.get with playlist parameters")
                                
                                # Fallback: пытаемся получить треки через audio.get
                                fallback_params = {
                                    'access_token': token,
                                    'v': self.api_version,
                                    'owner_id': owner_id,
                                    'playlist_id': playlist_id,
                                    'count': 50  # Максимум треков
                                }
                                
                                if access_hash:
                                    fallback_params['access_hash'] = access_hash
                                
                                async with session.get(f"{self.base_url}/audio.get", params=fallback_params) as fallback_response:
                                    if fallback_response.status == 200:
                                        fallback_data = await fallback_response.json()
                                        
                                        if 'error' in fallback_data:
                                            error_code = fallback_data['error'].get('error_code')
                                            error_msg = fallback_data['error'].get('error_msg', '')
                                            logger.warning(f"VK API fallback error: {error_code} - {error_msg}")
                                            
                                            # Код 201 = доступ запрещен (приватный плейлист)
                                            if error_code == 201:
                                                logger.info("Playlist is private (error 201)")
                                            return []
                                        
                                        # Пытаемся извлечь треки из fallback ответа
                                        fallback_resp = fallback_data.get("response")
                                        if isinstance(fallback_resp, dict) and "items" in fallback_resp:
                                            items = fallback_resp["items"]
                                            logger.info(f"✅ Fallback successful: got {len(items)} items via audio.get")
                                        else:
                                            logger.warning("Fallback failed: no items in audio.get response")
                                            return []
                                    else:
                                        logger.warning(f"Fallback request failed: {fallback_response.status}")
                                        return []
                                
                        elif isinstance(response, list):
                            # Список - используем напрямую (редкий случай)
                            items = response
                            logger.debug("Using response as list")
                            
                        else:
                            # Неожиданный формат
                            logger.warning(f"Unexpected playlist response format: {type(response)}")
                            return []
                        
                        logger.info(f"📊 VK playlist returned {len(items)} items")
                        
                        tracks = []
                        for item in items:
                            try:
                                # Проверка: строка или объект?
                                if isinstance(item, str):
                                    # Парсим строку: "owner_id_audio_id_access_key"
                                    parsed = self._parse_audio_string(item)
                                    if not parsed:
                                        continue
                                    
                                    # Получаем полный объект через audio.getById
                                    track = await self.get_track_by_url(
                                        parsed['owner_id'], 
                                        parsed['audio_id'], 
                                        parsed.get('access_key')
                                    )
                                    
                                    if not track:
                                        logger.debug(f"Track not found for string: {item}")
                                        continue
                                else:
                                    # Обычный объект
                                    track = VKTrack.from_vk_api_response(item)
                                
                                if track and track.is_valid and len(tracks) < 50:  # Максимум 50 валидных треков
                                    tracks.append(track)
                                    logger.debug(f"Added track: '{track.title}' by '{track.artist}'")
                                else:
                                    if len(tracks) >= 50:
                                        break
                            except Exception as e:
                                logger.warning(f"Error parsing track: {e}")
                                continue
                        
                        logger.info(f"✅ VK playlist extracted: {len(tracks)} valid tracks")
                        return tracks
                    else:
                        logger.error(f"VK API error: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"VK playlist error: {e}")
            return []
    
    async def get_tracks_from_audios_page(self, owner_id: int, section: str = 'all', max_tracks: int = 50) -> List[VKTrack]:
        """
        Получить треки со страницы аудио пользователя
        
        Args:
            owner_id: ID владельца страницы
            section: Секция (all, playlists, albums)
            max_tracks: Максимальное количество треков
            
        Returns:
            Список треков (до max_tracks)
        """
        try:
            # Используем случайный токен
            token, token_preview = self._get_random_token()
            logger.info(f"🎵 Getting VK audios page: owner={owner_id}, section={section} (Token: {token_preview})")
            
            # Мягкий SSL контекст
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            tracks = []
            
            # В зависимости от section выбираем метод API
            if section == 'all' or section not in ['playlists', 'albums']:
                # Используем audio.get для получения всех аудио
                params = {
                    'access_token': token,
                    'v': self.api_version,
                    'owner_id': owner_id,
                    'count': min(max_tracks, 100)  # VK API лимит
                }
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(f"{self.base_url}/audio.get", params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            if 'error' in data:
                                error_code = data['error'].get('error_code')
                                error_msg = data['error'].get('error_msg', '')
                                logger.warning(f"VK API Error: {error_code} - {error_msg}")
                                
                                # Если токен истёк (error_code 5) - выбрасываем исключение
                                if error_code == 5:
                                    self.dead_tokens.add(token)
                                    logger.critical(f"VK token expired and marked as dead: {token_preview}")
                                    logger.warning(f"Active tokens remaining: {len(self.tokens) - len(self.dead_tokens)}/{len(self.tokens)}")
                                    raise VKTokenExpiredError(
                                        token_preview=token_preview,
                                        full_token=token,
                                        error_details=data['error']
                                    )
                                
                                # Код 201 = доступ запрещен (приватная страница)
                                if error_code == 201:
                                    logger.info("Audios page is private")
                                return []
                            
                            if 'response' in data and 'items' in data['response']:
                                items = data['response']['items']
                                logger.info(f"📊 VK audios page returned {len(items)} tracks")
                                
                                for item in items:
                                    if len(tracks) >= max_tracks:
                                        break
                                    try:
                                        # Проверка: строка или объект?
                                        if isinstance(item, str):
                                            # Парсим строку
                                            parsed = self._parse_audio_string(item)
                                            if not parsed:
                                                continue
                                            
                                            # Получаем полный объект
                                            track = await self.get_track_by_url(
                                                parsed['owner_id'], 
                                                parsed['audio_id'], 
                                                parsed.get('access_key')
                                            )
                                            
                                            if not track:
                                                continue
                                        else:
                                            # Обычный объект
                                            track = VKTrack.from_vk_api_response(item)
                                        
                                        if track and track.is_valid:
                                            tracks.append(track)
                                    except Exception as e:
                                        logger.warning(f"Error parsing track: {e}")
                                        continue
            
            elif section == 'playlists':
                # Для плейлистов - получаем список плейлистов, затем треки из первого
                params = {
                    'access_token': token,
                    'v': self.api_version,
                    'owner_id': owner_id,
                    'count': 1  # Берем первый плейлист
                }
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(f"{self.base_url}/audio.getPlaylists", params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            if 'error' in data:
                                logger.warning(f"VK API Error: {data['error']}")
                                return []
                            
                            if 'response' in data and 'items' in data['response']:
                                playlists = data['response']['items']
                                if playlists:
                                    first_playlist = playlists[0]
                                    playlist_id = first_playlist.get('id')
                                    access_hash = first_playlist.get('access_hash')
                                    
                                    # Получаем треки из первого плейлиста
                                    tracks = await self.get_playlist(owner_id, playlist_id, access_hash)
            
            logger.info(f"✅ Audios page extracted: {len(tracks)} valid tracks")
            return tracks[:max_tracks]
            
        except Exception as e:
            logger.error(f"VK audios page error: {e}")
            return []
    
    async def get_tracks_from_post(self, owner_id: int, post_id: int, max_tracks: int = 50) -> List[VKTrack]:
        """
        Получить треки из поста на стене
        
        Args:
            owner_id: ID владельца поста
            post_id: ID поста
            max_tracks: Максимальное количество треков
            
        Returns:
            Список треков из вложений поста
        """
        try:
            # Используем случайный токен
            token, token_preview = self._get_random_token()
            logger.info(f"🎵 Getting VK post: {owner_id}_{post_id} (Token: {token_preview})")
            
            params = {
                'access_token': token,
                'v': self.api_version,
                'posts': f"{owner_id}_{post_id}"
            }
            
            # Мягкий SSL контекст
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{self.base_url}/wall.getById", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if 'error' in data:
                            error_code = data['error'].get('error_code')
                            error_msg = data['error'].get('error_msg', '')
                            logger.warning(f"VK API Error: {error_code} - {error_msg}")
                            
                            # Если токен истёк (error_code 5) - выбрасываем исключение
                            if error_code == 5:
                                self.dead_tokens.add(token)
                                logger.critical(f"VK token expired and marked as dead: {token_preview}")
                                logger.warning(f"Active tokens remaining: {len(self.tokens) - len(self.dead_tokens)}/{len(self.tokens)}")
                                raise VKTokenExpiredError(
                                    token_preview=token_preview,
                                    full_token=token,
                                    error_details=data['error']
                                )
                            return []
                        
                        if 'response' in data and len(data['response']) > 0:
                            post = data['response'][0]
                            attachments = post.get('attachments', [])
                            
                            logger.info(f"📊 Post has {len(attachments)} attachments")
                            
                            tracks = []
                            
                            for attachment in attachments:
                                if len(tracks) >= max_tracks:
                                    break
                                
                                attach_type = attachment.get('type')
                                
                                # Если это аудио - добавляем напрямую
                                if attach_type == 'audio':
                                    try:
                                        audio_data = attachment.get('audio', {})
                                        
                                        # Проверка: строка или объект?
                                        if isinstance(audio_data, str):
                                            # Парсим строку
                                            parsed = self._parse_audio_string(audio_data)
                                            if not parsed:
                                                continue
                                            
                                            # Получаем полный объект
                                            track = await self.get_track_by_url(
                                                parsed['owner_id'], 
                                                parsed['audio_id'], 
                                                parsed.get('access_key')
                                            )
                                        else:
                                            # Обычный объект
                                            track = VKTrack.from_vk_api_response(audio_data)
                                        
                                        if track and track.is_valid:
                                            tracks.append(track)
                                            logger.debug(f"Added audio: {track.title}")
                                    except Exception as e:
                                        logger.warning(f"Error parsing audio attachment: {e}")
                                
                                # Если это плейлист - получаем все треки
                                elif attach_type == 'audio_playlist':
                                    try:
                                        playlist_data = attachment.get('audio_playlist', {})
                                        pl_owner_id = playlist_data.get('owner_id')
                                        pl_id = playlist_data.get('id')
                                        pl_access_key = playlist_data.get('access_key')
                                        
                                        if pl_owner_id and pl_id:
                                            # Рекурсивно получаем треки из плейлиста
                                            playlist_tracks = await self.get_playlist(
                                                pl_owner_id, 
                                                pl_id, 
                                                pl_access_key
                                            )
                                            
                                            # Добавляем треки с учетом лимита
                                            for track in playlist_tracks:
                                                if len(tracks) >= max_tracks:
                                                    break
                                                tracks.append(track)
                                            
                                            logger.debug(f"Added {len(playlist_tracks)} tracks from playlist")
                                    except Exception as e:
                                        logger.warning(f"Error parsing audio_playlist attachment: {e}")
                            
                            logger.info(f"✅ Post extracted: {len(tracks)} valid tracks")
                            return tracks[:max_tracks]
                        else:
                            logger.warning("Post not found or empty")
                            return []
                    else:
                        logger.error(f"VK API error: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"VK post error: {e}")
            return []