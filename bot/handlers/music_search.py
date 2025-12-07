"""
Обработчик поиска музыки через VK API
"""

import os
import logging
import asyncio

logger = logging.getLogger(__name__)
from typing import List, Optional, Dict, Any
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from pathlib import Path
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiohttp
from collections import deque, OrderedDict

from bot.services.vk_api import VKAPI
from bot.models.vk_track import VKTrack
from bot.constants.messages import SEARCH_START_MESSAGE, SEARCH_RESULTS_MESSAGE
# Убрали импорт NEW_SEARCH_BUTTON - кнопка удалена
from bot.keyboards.inline import create_track_keyboard
from bot.keyboards.pagination import create_pagination_keyboard
from bot.config.settings import settings

logger = logging.getLogger(__name__)
router = Router()

# Состояния для FSM
class SearchStates(StatesGroup):
    waiting_for_query = State()
    showing_results = State()


class MusicSearchHandler:
    """Универсальный обработчик поиска музыки"""
    
    def __init__(self, rate_limiter=None, admin_notifier=None):
        self.vk_api = None
        self.rate_limiter = rate_limiter  # Сервис лимитов
        self.admin_notifier = admin_notifier  # Система уведомлений администратора
        self.search_cache = OrderedDict()  # Кеш результатов поиска (устарел, теперь в Redis)
        
        # Ограничения кэша поиска
        self._max_cache_entries = 500      # Максимум 500 записей глобально
        self._max_cache_per_user = 20      # Максимум 20 записей на пользователя
        self._cache_ttl = 30 * 60          # 30 минут TTL
        
        self.downloaded_tracks = {}  # Кеш скачанных треков
        self.post_image_enabled = False  # Тумблер для картинки поста (отключено)
        self._user_queues = {}  # {user_id: {'active': set(), 'queue': deque()}}
        
        # Лимиты очереди (можно легко изменить)
        self._max_active_per_user = 2        # Максимум 2 активных скачивания
        self._max_queue_per_user = 3         # Максимум 3 трека в очереди
        self._global_max_downloads = 30      # Максимум 30 глобальных скачиваний
        self._background_tasks = set()  # Все задачи для отслеживания
        self._file_sender_bot = None  # Отдельный Bot instance для отправки файлов (создастся при первом использовании)
        
        # Учёт файлов в assets/temp (LRU кэш)
        self._file_registry = {}  # {file_path: {'size': int, 'last_access': float}}
        self._total_storage_size = 0
        self._max_storage_gb = 10  # 10 ГБ максимум
        
        # Инициализация хранилища и задач будет запущена в main.py после создания бота
        
        # Инициализируем VK API если есть токены
        if settings.vk_tokens:
            try:
                self.vk_api = VKAPI(tokens=settings.vk_tokens)
                logger.info(f"VK API initialized with {len(settings.vk_tokens)} token(s)")
            except Exception as e:
                logger.error(f"VK API initialization failed: {e}")
                self.vk_api = None
        else:
            logger.warning("VK tokens not found, VK disabled")
        
        # Инициализируем YouTube API
        try:
            from bot.services.youtube_api import YouTubeAPI
            self.youtube_api = YouTubeAPI()
            logger.info("YouTube API initialized (via yt-dlp)")
        except Exception as e:
            logger.error(f"YouTube API initialization failed: {e}")
            self.youtube_api = None
        
        # Инициализируем SoundCloud API
        try:
            from bot.services.soundcloud_api import SoundCloudAPI
            self.soundcloud_api = SoundCloudAPI()
            logger.info("SoundCloud API initialized (via yt-dlp)")
        except Exception as e:
            logger.error(f"SoundCloud API initialization failed: {e}")
            self.soundcloud_api = None
    
    def _calculate_max_tracks_by_chars(self, tracks: List[Dict[str, Any]], max_chars: int) -> int:
        """Умно рассчитывает максимальное количество треков по символам"""
        total_chars = 0
        max_tracks = 0
        
        for track_info in tracks:
            track = track_info['track']
            
            # Получаем время в зависимости от типа трека
            if hasattr(track, 'formatted_duration'):
                duration = track.formatted_duration
            elif hasattr(track, 'duration_str'):
                duration = track.duration_str
            else:
                duration = "0:00"  # Fallback
            
            # Считаем реальную длину трека: "3:45 | Название"
            if len(track.title) > 27:
                track_length = len(f"{duration} | {track.title[:27]}...")
            else:
                track_length = len(f"{duration} | {track.title}")
            
            if total_chars + track_length <= max_chars:
                total_chars += track_length
                max_tracks += 1
            else:
                break
        
        return max_tracks
    
    def _is_valid_music_track(self, track, query: str) -> bool:
        """Проверяет, является ли трек подходящей музыкой"""
        # Проверка длительности (20 секунд - 60 минут)
        if hasattr(track, 'duration') and track.duration:
            if track.duration < 20 or track.duration > 3600:
                return False
        
        # Проверка размера файла (если известен)
        if hasattr(track, 'file_size') and track.file_size:
            max_size_bytes = 100 * 1024 * 1024  # 100 МБ
            if track.file_size > max_size_bytes:
                return False
        
        return True
    
    def _truncate_title(self, title: str, max_length: int = 25) -> str:
        """Обрезает название трека до указанной длины"""
        if len(title) <= max_length:
            return title
        return title[:max_length-3] + "..."
    
    def _truncate_artist(self, artist: str, max_length: int = 15) -> str:
        """Обрезает исполнителя до указанной длины"""
        if len(artist) <= max_length:
            return artist
        return artist[:max_length-3] + "..."
    
    def _get_user_queue(self, user_id: int) -> dict:
        """Получить или создать очередь пользователя"""
        import time
        if user_id not in self._user_queues:
            self._user_queues[user_id] = {
                'active': set(),
                'queue': deque(),
                'last_activity': time.time()
            }
        else:
            self._user_queues[user_id]['last_activity'] = time.time()
        return self._user_queues[user_id]
    
    async def _start_download_task(self, callback, track_info, user_id):
        """Запустить задачу скачивания"""
        user_queue = self._get_user_queue(user_id)
        
        # Создаем задачу
        task = asyncio.create_task(
            self._download_and_send_track_detached(
                bot_token=callback.bot.token,
                chat_id=callback.message.chat.id,
                track_info=track_info,
                user_id=user_id  # Передаем user_id
            )
        )
        
        # Добавляем в активные
        user_queue['active'].add(task)
        self._background_tasks.add(task)
        
        # Когда задача завершится - запустить следующую из очереди
        task.add_done_callback(lambda t: asyncio.create_task(
            self._on_download_complete(t, user_id)
        ))
    
    async def _on_download_complete(self, task, user_id):
        """Обработчик завершения скачивания"""
        user_queue = self._get_user_queue(user_id)
        
        # Удаляем из активных
        user_queue['active'].discard(task)
        self._background_tasks.discard(task)
        
        logger.debug(f"Download task completed for user {user_id}")
        logger.info(f"Active downloads: {len(user_queue['active'])}/{self._max_active_per_user}")
        logger.debug(f"Queue size: {len(user_queue['queue'])}/{self._max_queue_per_user}")
        
        # Если в очереди есть треки - запускаем следующий
        if user_queue['queue']:
            next_item = user_queue['queue'].popleft()
            logger.info(f"Starting queued download for user {user_id}, remaining: {len(user_queue['queue'])}")
            
            # Запускаем задачу
            await self._start_download_task_from_queue(next_item, user_id)
    
    async def _start_download_task_from_queue(self, queue_item, user_id):
        """Запустить задачу из очереди"""
        user_queue = self._get_user_queue(user_id)
        
        task = asyncio.create_task(
            self._download_and_send_track_detached(
                bot_token=queue_item['bot_token'],
                chat_id=queue_item['chat_id'],
                track_info=queue_item['track_info'],
                user_id=user_id
            )
        )
        
        user_queue['active'].add(task)
        self._background_tasks.add(task)
        
        task.add_done_callback(lambda t: asyncio.create_task(
            self._on_download_complete(t, user_id)
        ))
    
    async def _deserialize_cached_tracks(self, cached_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Конвертирует кешированные данные обратно в track_info формат
        
        Args:
            cached_data: Список сериализованных треков из кеша
            
        Returns:
            Список track_info с объектами Track
        """
        result = []
        
        for cached_track in cached_data:
            source = cached_track['source']
            
            # Восстанавливаем объект Track в зависимости от источника
            if source == 'vk':
                track = VKTrack(
                    id=cached_track['id'],
                    owner_id=cached_track['owner_id'],
                    title=cached_track['title'],
                    artist=cached_track['artist'],
                    duration=cached_track['duration'],
                    url=''  # URL не кешируется, будет получен при скачивании
                )
            elif source == 'youtube':
                from bot.models.youtube_track import YouTubeTrack
                track = YouTubeTrack(
                    id=cached_track['id'],
                    title=cached_track['title'],
                    channel=cached_track.get('artist', 'Unknown'),  # artist = channel
                    duration=cached_track['duration'],
                    webpage_url=cached_track.get('webpage_url', f"https://www.youtube.com/watch?v={cached_track['id']}")
                )
            elif source == 'soundcloud':
                from bot.models.soundcloud_track import SoundCloudTrack
                track = SoundCloudTrack(
                    id=cached_track['id'],
                    title=cached_track['title'],
                    artist=cached_track.get('artist', 'Unknown'),
                    duration=cached_track['duration'],
                    permalink_url=cached_track.get('permalink_url', f"https://soundcloud.com/{cached_track['id']}")
                )
            else:
                logger.warning(f"Unknown source in cached track: {source}")
                continue
            
            track_info = {
                'track': track,
                'source': source,
                'display_name': cached_track['display_name'],
                'info_text': cached_track['info_text']
            }
            
            result.append(track_info)
        
        return result
    
    def _apply_smart_rotation(self, all_tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Возвращает треки как есть (только VK)
        """
        # Просто возвращаем все треки VK как есть
        return all_tracks
    
    
    async def start_search(self, message: Message, state: FSMContext):
        """Начало поиска музыки"""
        logger.info(f"User {message.from_user.id} started music search")
        await message.answer(SEARCH_START_MESSAGE, parse_mode="HTML")
        await state.set_state(SearchStates.waiting_for_query)
    
    async def handle_search_query(self, message: Message, state: FSMContext):
        """Обработка поискового запроса"""
        query = message.text.strip()
        user_id = message.from_user.id
        username = message.from_user.username or "Unknown"
        
        logger.info(f"🔎 Search query from {username} (ID: {user_id}): '{query}'")
        
        # Проверяем, является ли запрос ссылкой
        from bot.utils.url_parser import parse_url
        
        parsed_url = parse_url(query)
        if parsed_url:
            logger.info(f"Detected {parsed_url.source} {parsed_url.type}: {query}")
            
            # Обработка ссылки
            if parsed_url.type in ['playlist', 'audios_page', 'post']:
                await self._handle_playlist_link(message, parsed_url, user_id)
            else:  # track
                await self._handle_track_link(message=message, parsed_url=parsed_url, user_id=user_id)
            return
        
        # Валидация запроса
        if len(query) < 2:
            await message.answer("❌ Запрос слишком короткий. Минимум 2 символа.")
            return
        
        if len(query) > 100:
            await message.answer("❌ Запрос слишком длинный. Максимум 100 символов.")
            return
        
        # Проверяем кеш результатов поиска (если есть rate_limiter)
        all_tracks = None
        if self.rate_limiter:
            all_tracks = await self.rate_limiter.get_cached_search(query)
            if all_tracks:
                logger.info(f"Using cached results for '{query}': {len(all_tracks)} tracks")
                # Конвертируем из сериализованного формата обратно в track_info
                all_tracks = await self._deserialize_cached_tracks(all_tracks)
        
        # Проверяем лимиты (если нет кеша)
        if not all_tracks and self.rate_limiter:
            allowed, wait_minutes, limit_message = await self.rate_limiter.check_limit(user_id)
            if not allowed:
                await message.answer(limit_message)
                return
            
            # Регистрируем операцию поиска
            await self.rate_limiter.register_operation(user_id)
        
        # Показываем сообщение о поиске
        search_msg = await message.answer("🔍 Ищу музыку...")
        
        try:
            # Поиск по всем источникам (если не было в кеше)
            if not all_tracks:
                all_tracks = await self._search_all_sources(query)
                
                # Кешируем результаты (если есть rate_limiter)
                if all_tracks and self.rate_limiter:
                    await self.rate_limiter.cache_search(query, all_tracks)
            
            if not all_tracks:
                try:
                    await search_msg.edit_text("❌ Ничего не найдено. Попробуйте другой запрос.")
                except Exception as e:
                    logger.warning(f"Failed to edit search message: {e}")
                    await search_msg.answer("❌ Ничего не найдено. Попробуйте другой запрос.")
                return
            
            # Сохраняем результаты в кеш с timestamp
            import time
            self.search_cache[str(user_id)] = {
                'query': query,
                'tracks': all_tracks,
                'stored_at': time.time()
            }
            
            # Показываем результаты
            await self._show_search_results(search_msg, all_tracks, query, user_id, 1, all_tracks)
            if state:
                await state.set_state(SearchStates.showing_results)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            try:
                if search_msg.text or search_msg.caption:
                    await search_msg.edit_text("❌ Ошибка при поиске. Попробуйте позже.")
                else:
                    await search_msg.answer("❌ Ошибка при поиске. Попробуйте позже.")
            except Exception as edit_error:
                logger.warning(f"Failed to edit error message: {edit_error}")
                await search_msg.answer("❌ Ошибка при поиске. Попробуйте позже.")
    
    async def _search_all_sources(self, query: str) -> List[Dict[str, Any]]:
        """Поиск по всем доступным источникам с таймаутом 10 секунд"""
        try:
            # Общий таймаут 10 секунд на весь поиск
            return await asyncio.wait_for(self._do_actual_search(query), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("⏰ Поиск превысил 10 секунд, возвращаем что нашли")
            return []
        except Exception as e:
            logger.error(f"Search timeout error: {e}")
            return []
    
    async def _do_actual_search(self, query: str) -> List[Dict[str, Any]]:
        """Фактический поиск по VK"""
        all_tracks = []
        
        # Поиск на VK (если доступен)
        if self.vk_api:
            try:
                logger.info(f"🎵 VK search: '{query}' (limit 50)")
                vk_tracks = await self.vk_api.search_tracks(query, max_results=50)
                logger.info(f"📊 Found {len(vk_tracks)} tracks")
                vk_added = 0
                for track in vk_tracks:
                    # Фильтруем неподходящие результаты
                    if self._is_valid_music_track(track, query):
                        all_tracks.append({
                            'track': track,
                            'source': 'vk',
                            'display_name': track.display_name,
                            'info_text': track.info_text
                        })
                        vk_added += 1
                logger.debug(f"VK: Found {len(vk_tracks)} tracks, added {vk_added} after filtering")
            except Exception as e:
                # Импортируем исключение для проверки типа
                from bot.services.vk_api import VKTokenExpiredError
                
                # Специальная обработка истечения токена
                if isinstance(e, VKTokenExpiredError):
                    logger.critical(f"🚨 VK token expired: {e.token_preview}")
                    logger.error(f"Error details: {e.error_details}")
                    
                    # Уведомление админу с детальной инструкцией
                    if hasattr(self, 'admin_notifier') and self.admin_notifier:
                        message = (
                            f"🚨 VK token expired: {e.token_preview}\n\n"
                            f"📋 Действия:\n"
                            f"1. Откройте .env файл\n"
                            f"2. Найдите и удалите токен: {e.token_preview}\n"
                            f"3. Получите новый токен от VK\n"
                            f"4. Добавьте новый токен в VK_TOKENS\n"
                            f"5. Перезапустите бота: systemctl restart amusic-bot\n\n"
                            f"⚠️ Токен исключён из ротации до перезапуска.\n"
                            f"Активных токенов: {len(self.vk_api.tokens) - len(self.vk_api.dead_tokens)}/{len(self.vk_api.tokens)}"
                        )
                        await self.admin_notifier.notify(
                            level="CRITICAL",
                            message=message,
                            event_type="vk_token_expired"
                        )
                else:
                    # Обычная ошибка поиска
                    logger.error(f"VK search error: {e}")
                    
                    # Уведомление админу
                    if hasattr(self, 'admin_notifier') and self.admin_notifier:
                        await self.admin_notifier.notify(
                            level="ERROR",
                            message=f"VK search error: {e}",
                            event_type="search_error"
                        )
        else:
            logger.warning("VK API not available")
        
        return all_tracks
    
    async def _handle_playlist_link(self, message: Message, parsed_url, user_id: int):
        """Обработка ссылки на плейлист"""
        try:
            # Показываем сообщение о загрузке
            loading_msg = await message.answer("🔍 Загружаю плейлист...")
            
            all_tracks = []
            
            # Получаем треки в зависимости от источника
            if parsed_url.source == 'vk' and self.vk_api:
                # Определяем тип VK ссылки и получаем треки
                if parsed_url.type == 'playlist':
                    owner_id = parsed_url.ids['owner_id']
                    playlist_id = parsed_url.ids['playlist_id']
                    access_hash = parsed_url.ids.get('access_hash')
                    
                    vk_tracks = await self.vk_api.get_playlist(owner_id, playlist_id, access_hash)
                    
                elif parsed_url.type == 'audios_page':
                    owner_id = parsed_url.ids['owner_id']
                    section = parsed_url.ids.get('section', 'all')
                    
                    vk_tracks = await self.vk_api.get_tracks_from_audios_page(owner_id, section)
                    
                elif parsed_url.type == 'post':
                    owner_id = parsed_url.ids['owner_id']
                    post_id = parsed_url.ids['post_id']
                    
                    vk_tracks = await self.vk_api.get_tracks_from_post(owner_id, post_id)
                else:
                    vk_tracks = []
                
                # Преобразуем VK треки в единый формат
                for track in vk_tracks:
                    all_tracks.append({
                        'track': track,
                        'source': 'vk',
                        'display_name': track.display_name,
                        'info_text': track.info_text
                    })
            
            elif parsed_url.source == 'youtube' and self.youtube_api:
                yt_tracks = await self.youtube_api.extract_playlist_info(parsed_url.url, max_tracks=50)
                for track in yt_tracks:
                    all_tracks.append({
                        'track': track,
                        'source': 'youtube',
                        'display_name': track.display_name,
                        'info_text': track.info_text
                    })
            
            elif parsed_url.source == 'soundcloud' and self.soundcloud_api:
                sc_tracks = await self.soundcloud_api.extract_playlist_info(parsed_url.url, max_tracks=50)
                for track in sc_tracks:
                    all_tracks.append({
                        'track': track,
                        'source': 'soundcloud',
                        'display_name': track.display_name,
                        'info_text': track.info_text
                    })
            
            else:
                await loading_msg.edit_text("❌ Источник не поддерживается или недоступен.")
                return
            
            if not all_tracks:
                # Формируем вежливое сообщение в зависимости от типа
                if parsed_url.type == 'audios_page':
                    error_msg = "🎵 На этой странице не найдено доступных аудиозаписей.\n\nВозможно, страница приватная или аудио были удалены."
                elif parsed_url.type == 'post':
                    error_msg = "🎵 В этом посте не найдено аудиозаписей или плейлистов.\n\nПроверьте, что пост содержит музыку."
                else:  # playlist
                    error_msg = "❌ Плейлист пуст или недоступен.\n\nВозможно, он приватный или был удален."
                
                await loading_msg.edit_text(error_msg + "\n\nПопробуйте другую ссылку или напишите @boshck")
                return
            
            # Сохраняем результаты в кеш
            import time
            self.search_cache[str(user_id)] = {
                'query': parsed_url.url,
                'tracks': all_tracks,
                'stored_at': time.time()
            }
            
            # Показываем результаты
            await self._show_search_results(loading_msg, all_tracks, f"Плейлист ({len(all_tracks)} треков)", user_id, 1, all_tracks)
            
        except Exception as e:
            logger.error(f"Playlist link handling error: {e}")
            
            # Уведомление админу
            if self.admin_notifier:
                await self.admin_notifier.notify(
                    level="ERROR",
                    message=f"Playlist link error ({parsed_url.source}): {e}",
                    event_type="search_error"
                )
            
            await message.answer("❌ Ошибка при загрузке плейлиста. Попробуйте другую ссылку или напишите @boshck")
    
    async def _handle_track_link(self, message: Message, parsed_url, user_id: int):
        """Обработка ссылки на одиночный трек"""
        try:
            # Проверяем лимиты
            if self.rate_limiter:
                allowed, wait_minutes, limit_message = await self.rate_limiter.check_limit(user_id)
                if not allowed:
                    await message.answer(limit_message)
                    return
                
                # Регистрируем операцию
                await self.rate_limiter.register_operation(user_id)
            
            # Показываем сообщение о загрузке
            loading_msg = await message.answer("🔍 Получаю информацию о треке...")
            
            track = None
            source = parsed_url.source
            
            # Получаем трек в зависимости от источника
            if source == 'vk' and self.vk_api:
                owner_id = parsed_url.ids['owner_id']
                audio_id = parsed_url.ids['audio_id']
                access_key = parsed_url.ids.get('access_key')
                track = await self.vk_api.get_track_by_url(owner_id, audio_id, access_key)
            
            elif source == 'youtube' and self.youtube_api:
                track = await self.youtube_api.extract_track_info(parsed_url.url)
            
            elif source == 'soundcloud' and self.soundcloud_api:
                track = await self.soundcloud_api.extract_track_info(parsed_url.url)
            
            else:
                await loading_msg.edit_text("❌ Источник не поддерживается или недоступен.")
                return
            
            if not track or not track.is_valid:
                await loading_msg.edit_text("❌ Трек недоступен для скачивания. Попробуйте другую ссылку или напишите @boshck")
                return
            
            # Проверяем лимиты очереди
            user_queue = self._get_user_queue(user_id)
            total_user_tasks = len(user_queue['active']) + len(user_queue['queue'])
            
            if total_user_tasks >= (self._max_active_per_user + self._max_queue_per_user):
                await loading_msg.edit_text(
                    f"⚠️ Максимум {self._max_active_per_user + self._max_queue_per_user} треков за раз!\n"
                    f"Активных: {len(user_queue['active'])}\n"
                    f"В очереди: {len(user_queue['queue'])}\n"
                    "Дождитесь завершения загрузок."
                )
                return
            
            # Создаем track_info для скачивания
            track_info = {
                'track': track,
                'source': source,
                'display_name': track.display_name,
                'info_text': track.info_text
            }
            
            await loading_msg.edit_text(f"⬇️ Начинаю скачивание: {track.display_name}")
            
            # Запускаем скачивание напрямую
            # Создаем псевдо-callback для совместимости с существующим кодом
            from types import SimpleNamespace
            
            class FakeCallback:
                def __init__(self, bot, chat_id):
                    self.bot = bot
                    self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
            
            fake_callback = FakeCallback(message.bot, message.chat.id)
            
            await self._start_download_task(fake_callback, track_info, user_id)
            
        except Exception as e:
            logger.error(f"Track link handling error: {e}")
            
            # Уведомление админу
            if self.admin_notifier:
                await self.admin_notifier.notify(
                    level="ERROR",
                    message=f"Track link error ({parsed_url.source}): {e}",
                    event_type="download_error"
                )
            
            await message.answer("❌ Ошибка при загрузке трека. Попробуйте другую ссылку или напишите @boshck")
    
    async def _show_search_results(self, message: Message, tracks: List[Dict[str, Any]], query: str, user_id: int, page: int = 1, all_tracks: List[Dict[str, Any]] = None):
        """Показывает результаты поиска с пагинацией"""
        if not tracks:
            await message.edit_text("❌ Ничего не найдено.")
            return
        
        # Пагинация: 10 треков на страницу
        tracks_per_page = 10
        total_tracks = len(all_tracks) if all_tracks else len(tracks)
        total_pages = (total_tracks + tracks_per_page - 1) // tracks_per_page
        
        # Ограничиваем страницу
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        # Получаем треки для текущей страницы
        source_tracks = all_tracks if all_tracks else tracks
        start_idx = (page - 1) * tracks_per_page
        end_idx = start_idx + tracks_per_page
        page_tracks = source_tracks[start_idx:end_idx]
        
        # Создаем текст сообщения только с треками текущей страницы
        results_text = ""
        for i, track_info in enumerate(page_tracks, start_idx + 1):
            track = track_info['track']
            results_text += f"{i}. {track_info['display_name']}\n"
            results_text += f"   {track_info['info_text']}\n\n"
        
        # Создаем клавиатуру с пагинацией
        keyboard = self._create_tracks_keyboard(page_tracks, query, user_id, page, total_pages)
        
        # Отправляем или редактируем сообщение
        if self.post_image_enabled:
            try:
                # Пытаемся отправить с картинкой
                image_path = Path("assets/covers/search_post.jpg")
                if image_path.exists():
                    # Подпись к фото: только запрос и общее количество результатов
                    caption_text = f"<b>{query}</b>\n<b>🎵 Найдено треков: {total_tracks}</b>"
                    
                    photo = FSInputFile(image_path)
                    await message.delete()
                    await message.answer_photo(
                        photo=photo,
                        caption=caption_text,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    return
            except Exception as e:
                logger.warning(f"Failed to send with image: {e}")
        
        # Fallback: обычное сообщение с заголовком (только заголовок, без списка треков)
        try:
            # Только заголовок: запрос + количество треков
            full_text = f"<b>{query}</b>\n\n<b>🎵 Найдено треков: {total_tracks}</b>"
            await message.edit_text(full_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            # Если не можем редактировать, отправляем новое сообщение (только заголовок)
            logger.warning(f"Failed to edit message: {e}")
            full_text = f"<b>{query}</b>\n\n<b>🎵 Найдено треков: {total_tracks}</b>"
            await message.answer(full_text, reply_markup=keyboard, parse_mode="HTML")
    
    def _create_tracks_keyboard(self, tracks: List[Dict[str, Any]], query: str, user_id: int, page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
        """Создает клавиатуру с треками и пагинацией"""
        keyboard = []
        
        # Кнопки для треков текущей страницы
        for i, track_info in enumerate(tracks, 1):
            track = track_info['track']
            source = track_info['source']
            
            # Иконка в зависимости от источника
            if source == 'vk':
                source_icon = "🎶"
            elif source == 'youtube':
                source_icon = "🎬"
            elif source == 'soundcloud':
                source_icon = "🎧"
            else:
                source_icon = "🎵"
            
            # Создаем кнопку для трека: "3:45 🎶 Название - Исполнитель"
            # Получаем время в зависимости от типа трека
            if hasattr(track, 'formatted_duration'):
                duration = track.formatted_duration
            elif hasattr(track, 'duration_str'):
                duration = track.duration_str
            else:
                duration = "0:00"  # Fallback
            
            # Обрезаем название трека
            truncated_title = self._truncate_title(track.title, 25)
            
            # Получаем исполнителя (учитываем разные атрибуты)
            if hasattr(track, 'artist'):
                artist = track.artist
            elif hasattr(track, 'channel'):
                artist = track.channel
            else:
                artist = "Unknown"
            
            truncated_artist = self._truncate_artist(artist, 15)
            # Формат кнопки: "3:45 🎶 Название - Исполнитель"
            button_text = f"{duration} {source_icon} {truncated_title} - {truncated_artist}"
            
            # Убрали query и user_id для экономии байтов (лимит 64 байта)
            callback_data = f"download_{track_info['source']}_{track.id}"
            keyboard.append([InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data
            )])
        
        # Добавляем навигацию если страниц больше 1
        if total_pages > 1:
            nav_buttons = []
            
            # Кнопка "назад"
            prev_page = page - 1 if page > 1 else total_pages  # Циклическая навигация
            nav_buttons.append(InlineKeyboardButton(
                text="◀️",
                callback_data=f"page_{prev_page}"
            ))
            
            # Номер страницы
            nav_buttons.append(InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data="page_info"
            ))
            
            # Кнопка "вперед"
            next_page = page + 1 if page < total_pages else 1  # Циклическая навигация
            nav_buttons.append(InlineKeyboardButton(
                text="▶️",
                callback_data=f"page_{next_page}"
            ))
            
            keyboard.append(nav_buttons)
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    async def handle_pagination_callback(self, callback: CallbackQuery):
        """Обработка пагинации"""
        logger.debug(f"Pagination callback from user {callback.from_user.id}, data: {callback.data}")
        
        try:
            await callback.answer()  # Отвечаем сразу, чтобы избежать timeout
        except Exception as e:
            logger.warning(f"Callback answer failed (likely timeout): {e}")
            # Продолжаем работу, это не критично
        
        try:
            # Парсим callback_data: page_{page}
            data = callback.data.split('_')
            if len(data) < 2:
                logger.debug("Invalid callback data format")
                return  # Игнорируем некорректные данные
            
            if data[1] == "info":
                logger.debug("Info button clicked, ignoring")
                return  # Игнорируем кнопку с информацией о странице
            
            page = int(data[1])
            user_id = callback.from_user.id
            
            # Получаем данные из кеша по user_id
            user_cache = self.search_cache.get(str(user_id))
            if not user_cache:
                await callback.answer("❌ Результаты поиска устарели. Выполните поиск заново.", show_alert=True)
                return
            
            query = user_cache['query']
            tracks = user_cache['tracks']
            logger.debug(f"Found {len(tracks)} tracks in cache for query '{query}'")
            
            # Показываем результаты для указанной страницы
            await self._show_search_results(callback.message, tracks, query, user_id, page, tracks)
            logger.debug(f"Pagination: updated to page {page}")
            
        except Exception as e:
            logger.error(f"Pagination callback error: {e}")

    async def handle_download_callback(self, callback: CallbackQuery):
        """Обработка нажатия на кнопку скачивания"""
        answered = False  # Флаг для отслеживания вызова callback.answer()
        
        try:
            # Парсим callback_data: download_{source}_{track_id}
            # Используем split с ограничением, чтобы track_id мог содержать '_'
            data = callback.data.split('_', 2)  # Максимум 3 части
            if len(data) < 3:
                logger.debug(f"Invalid download callback data: {callback.data}")
                await callback.answer("❌ Ошибка в данных.", show_alert=True)
                return
            
            source = data[1]
            track_id = data[2]  # Теперь содержит полный ID с символами '_'
            user_id = callback.from_user.id
            
            logger.debug(f"Download callback: source={source}, track_id={track_id}, user={user_id}")
            
            # Проверяем лимиты (если есть rate_limiter)
            if self.rate_limiter:
                allowed, wait_minutes, limit_message = await self.rate_limiter.check_limit(user_id)
                if not allowed:
                    logger.warning(f"Rate limit exceeded for user {user_id}")
                    try:
                        await callback.answer(limit_message, show_alert=True)
                    except Exception as e:
                        logger.warning(f"Callback answer failed: {e}")
                    return
                
                # Регистрируем операцию скачивания
                await self.rate_limiter.register_operation(user_id)
            
            # Получаем данные из кеша по user_id
            user_cache = self.search_cache.get(str(user_id))
            if not user_cache:
                await callback.answer("❌ Результаты поиска устарели. Выполните поиск заново.", show_alert=True)
                return
            
            query = user_cache['query']
            tracks = user_cache['tracks']
            logger.debug(f"Found {len(tracks)} tracks in cache for query '{query}'")
            
            # Находим трек в списке
            track_info = None
            for t in tracks:
                if str(t['track'].id) == track_id and t['source'] == source:
                    track_info = t
                    break
            
            if not track_info:
                logger.debug("Track not found in cache")
                try:
                    await callback.answer("❌ Трек не найден.", show_alert=True)
                except Exception as e:
                    logger.warning(f"Callback answer failed (likely timeout): {e}")
                    # Продолжаем работу, это не критично
                return
            
            user_id = callback.from_user.id
            user_queue = self._get_user_queue(user_id)
            
            # Проверяем общий глобальный лимит
            if len(self._background_tasks) >= self._global_max_downloads:
                logger.warning(f"Global download limit reached: {len(self._background_tasks)}/{self._global_max_downloads}")
                try:
                    await callback.answer("Простите, пожалуйста, но кажется нас дудосят :( Скоро всё заработает!", show_alert=True)
                except Exception as e:
                    logger.warning(f"Callback answer failed (likely timeout): {e}")
                return
            
            # Проверяем общий лимит пользователя (активные + очередь)
            total_user_tasks = len(user_queue['active']) + len(user_queue['queue'])
            if total_user_tasks >= (self._max_active_per_user + self._max_queue_per_user):
                logger.debug(f"User limit reached: {total_user_tasks}/{self._max_active_per_user + self._max_queue_per_user}")
                try:
                    await callback.answer(
                        f"⚠️ Максимум {self._max_active_per_user + self._max_queue_per_user} треков за раз!\n"
                        f"Активных: {len(user_queue['active'])}\n"
                        f"В очереди: {len(user_queue['queue'])}\n"
                        "Дождитесь завершения загрузок.",
                        show_alert=True
                    )
                except Exception as e:
                    logger.warning(f"Callback answer failed (likely timeout): {e}")
                return
            
            # Если есть свободный слот - запускаем сразу
            if len(user_queue['active']) < self._max_active_per_user:
                logger.info(f"⬇️ Starting download: {len(user_queue['active'])}/{self._max_active_per_user} active")
                try:
                    await callback.answer("✅ Начинаю скачивание...")
                except Exception as e:
                    logger.warning(f"Callback answer failed (likely timeout): {e}")
                await self._start_download_task(callback, track_info, user_id)
            else:
                # Добавляем в очередь
                user_queue['queue'].append({
                    'callback': callback,
                    'track_info': track_info,
                    'bot_token': callback.bot.token,
                    'chat_id': callback.message.chat.id
                })
                position = len(user_queue['queue'])
                logger.debug(f"Added to queue: position {position}/{self._max_queue_per_user}")
                
                # Показываем "НЕ СПАМЬ" только при добавлении 3-го трека (индекс 2)
                try:
                    if position == 3:  # Последний разрешённый
                        await callback.answer(
                            f"⏳ Трек добавлен в очередь на позицию {position}.\n"
                            f"😈 НЕ СПАМЬ 😈",
                            show_alert=True
                        )
                    else:
                        await callback.answer(
                            f"⏳ Трек добавлен в очередь (позиция {position}/{self._max_queue_per_user})",
                            show_alert=False
                        )
                except Exception as e:
                    logger.warning(f"Callback answer failed (likely timeout): {e}")
            
            logger.debug(f"Download callback completed for user {user_id}")
            
        except Exception as e:
            logger.error(f"Download callback error: {e}")
            # Отвечаем только если ещё не отвечали
            if not answered:
                try:
                    await callback.answer("❌ Ошибка при скачивании.", show_alert=True)
                except Exception:
                    pass  # Игнорируем если уже был вызван
    
    async def _get_file_sender_bot(self, bot_token: str) -> Bot:
        """Получает или создаёт отдельный Bot instance для отправки файлов"""
        if self._file_sender_bot is None:
            # Создаём отдельный Bot instance (aiogram сам создаст session с нужными параметрами)
            self._file_sender_bot = Bot(
                token=bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            )
            logger.info("🤖 Создан отдельный Bot instance для отправки файлов")
        
        return self._file_sender_bot
    
    async def cleanup(self):
        """Graceful shutdown - закрытие всех сессий"""
        try:
            if self._file_sender_bot:
                session = getattr(self._file_sender_bot, 'session', None)
                if session and hasattr(session, 'closed') and not session.closed:
                    await session.close()
                    logger.info("File sender bot session closed")
        except Exception as e:
            logger.error(f"Error closing file sender bot session: {e}")
    
    async def _download_and_send_track_detached(self, bot_token: str, chat_id: int, track_info: Dict[str, Any], user_id: int = None):
        """Скачивает и отправляет трек БЕЗ привязки к callback (не блокирует другие события)"""
        track = track_info['track']
        source = track_info['source']
        
        logger.info(f"🎵 Downloading '{track.title}' by '{track.artist}'")
        
        # Получаем отдельный Bot instance для отправки файлов
        bot = await self._get_file_sender_bot(bot_token)
        
        try:
            # Определяем путь для сохранения
            temp_dir = Path("assets/temp")
            temp_dir.mkdir(exist_ok=True)
            
            # Создаем имя файла для кеширования (только VK)
            filename = track.get_safe_filename()
            file_path = temp_dir / filename
            
            # Проверяем, есть ли файл в кеше
            if file_path.exists():
                logger.debug(f"File found in cache: {file_path}")
                
                # Показываем статус "отправляет аудио"
                await bot.send_chat_action(chat_id=chat_id, action="upload_audio")
                
                # Отправляем файл из кеша
                audio_file = FSInputFile(file_path)
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    title=track.title,
                    performer=track.artist,
                    duration=track.duration,
                    caption='<a href="https://t.me/mp3dloader_bot">🎵 Скачать MP3</a>',
                    parse_mode='HTML'
                )
                
                logger.info(f"✅ Sent (cached): '{track.title}' → user {chat_id}")
                return
            
            # Показываем статус "записывает голосовое" во время скачивания
            await bot.send_chat_action(chat_id=chat_id, action="record_voice")
            
            # Скачиваем файл
            logger.info(f"⬇️ Downloading from {source.upper()}...")
            try:
                if source == 'vk' and self.vk_api:
                    file_path = await self.vk_api.download_audio(track)
                elif source == 'youtube' and self.youtube_api:
                    file_path = await self.youtube_api.download_audio(track)
                elif source == 'soundcloud' and self.soundcloud_api:
                    file_path = await self.soundcloud_api.download_audio(track)
                else:
                    raise Exception(f"Unknown source: {source}")
                
                if not file_path or not Path(file_path).exists():
                    raise Exception("File not found after download")
                
                logger.info(f"✅ Download completed: {os.path.basename(file_path)}")
                
                # Обновляем реестр файлов (LRU кэш)
                await self._update_file_registry(file_path)
                    
            except Exception as e:
                logger.error(f"Download failed for user {chat_id}: {e}")
                
                # Уведомление админу
                if self.admin_notifier:
                    await self.admin_notifier.notify(
                        level="ERROR",
                        message=f"Download failed: {e}",
                        event_type="download_error"
                    )
                return
            
            # Показываем статус "отправляет аудио"
            await bot.send_chat_action(chat_id=chat_id, action="upload_audio")
            
            # Обновляем last_access для LRU кэша
            if file_path in self._file_registry:
                import time
                self._file_registry[file_path]['last_access'] = time.time()
                logger.debug(f"LRU: updated last_access for {os.path.basename(file_path)}")
            
            # Отправляем файл
            audio_file = FSInputFile(file_path)
            await bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=track.title,
                performer=track.artist,
                duration=track.duration,
                caption='<a href="https://t.me/mp3dloader_bot">🎵 Скачать MP3</a>',
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Sent: '{track.title}' → user {chat_id}")
            
        except Exception as e:
            logger.error(f"Download/send error: {e}")
    
    async def cleanup_caches(self):
        """Удаляет просроченные и лишние записи из кэшей"""
        import time
        current_time = time.time()
        
        # Очистка просроченных записей (TTL)
        expired_keys = []
        for key, cache_data in list(self.search_cache.items()):
            # Проверяем что это dict с нужной структурой
            if isinstance(cache_data, dict) and 'stored_at' in cache_data:
                if current_time - cache_data['stored_at'] > self._cache_ttl:
                    expired_keys.append(key)
        
        for key in expired_keys:
            del self.search_cache[key]
        
        if expired_keys:
            logger.info(f"🧹 Удалено {len(expired_keys)} просроченных записей из кэша")
        
        # Ограничение по количеству записей
        while len(self.search_cache) > self._max_cache_entries:
            self.search_cache.popitem(last=False)
        
        logger.info(f"📊 Кэш: {len(self.search_cache)} записей")
    
    async def _cleanup_inactive_users(self):
        """Удаляет неактивных пользователей из очередей"""
        import time
        current_time = time.time()
        inactive_threshold = 30 * 60  # 30 минут
        
        inactive_users = []
        for user_id, queue_info in self._user_queues.items():
            if (not queue_info.get('active') and 
                not queue_info.get('queue') and
                current_time - queue_info.get('last_activity', current_time) > inactive_threshold):
                inactive_users.append(user_id)
        
        for user_id in inactive_users:
            del self._user_queues[user_id]
            logger.info(f"🧹 Удален неактивный пользователь: {user_id}")
        
        if inactive_users:
            logger.info(f"✅ Очищено {len(inactive_users)} неактивных пользователей")
    
    async def _start_cleanup_tasks(self):
        """Запускает периодические задачи очистки"""
        while True:
            try:
                await asyncio.sleep(300)  # Каждые 5 минут
                logger.debug("🔄 Запуск периодической очистки...")
                await self.cleanup_caches()
                await self._cleanup_inactive_users()
            except Exception as e:
                logger.error(f"Ошибка в задаче очистки: {e}")
                
                # Уведомление админу
                if hasattr(self, 'admin_notifier') and self.admin_notifier:
                    await self.admin_notifier.notify(
                        level="ERROR",
                        message=f"Cleanup task error: {e}",
                        event_type="cleanup_error"
                    )
    
    async def _init_storage(self):
        """Собирает информацию о существующих файлах при старте"""
        temp_dir = "assets/temp"
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir, exist_ok=True)
            return
        
        for filename in os.listdir(temp_dir):
            file_path = os.path.join(temp_dir, filename)
            if os.path.isfile(file_path):
                size = os.path.getsize(file_path)
                last_access = os.path.getatime(file_path)
                self._file_registry[file_path] = {
                    'size': size,
                    'last_access': last_access
                }
                self._total_storage_size += size
        
        logger.info(f"📊 Хранилище: {len(self._file_registry)} файлов, "
                    f"{self._total_storage_size / (1024**3):.2f} ГБ")
    
    async def start_background_tasks(self):
        """Запускает фоновые задачи после инициализации бота"""
        # Инициализация хранилища
        await self._init_storage()
        
        # Запуск периодических задач очистки
        asyncio.create_task(self._start_cleanup_tasks())
        
        # Health-check (по умолчанию выключен, раскомментируй для мониторинга)
        # asyncio.create_task(self._health_check_task())
        # logger.info("🏥 Health-check мониторинг запущен (каждые 10 минут)")
        
        logger.info("✅ Фоновые задачи управления ресурсами запущены")
    
    async def _update_file_registry(self, file_path: str):
        """Обновляет реестр файлов после успешной загрузки"""
        try:
            import time
            size = os.path.getsize(file_path)
            self._file_registry[file_path] = {
                'size': size,
                'last_access': time.time()
            }
            self._total_storage_size += size
            
            logger.info(f"📝 Файл добавлен: {os.path.basename(file_path)} ({size / (1024**2):.2f} МБ)")
            
            # Проверка лимита дискового пространства
            if self._total_storage_size > self._max_storage_gb * (1024**3):
                logger.warning(f"⚠️ Превышен лимит хранилища: {self._total_storage_size / (1024**3):.2f} ГБ")
                asyncio.create_task(self._cleanup_old_files())
                
        except Exception as e:
            logger.warning(f"Ошибка обновления реестра файлов {file_path}: {e}")
    
    async def _cleanup_old_files(self):
        """Удаляет самые старые файлы порциями по ~1 ГБ (LRU)"""
        logger.info("🧹 Запуск очистки дискового пространства...")
        
        # Сортируем файлы по времени последнего доступа (LRU)
        sorted_files = sorted(
            self._file_registry.items(),
            key=lambda x: x[1]['last_access']
        )
        
        freed_space = 0
        target_free_space = 1024**3  # 1 ГБ
        
        for file_path, file_info in sorted_files:
            if freed_space >= target_free_space:
                break
                
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    freed_space += file_info['size']
                    self._total_storage_size -= file_info['size']
                    del self._file_registry[file_path]
                    logger.info(f"🗑️ Удален файл: {os.path.basename(file_path)}")
            except Exception as e:
                logger.warning(f"Ошибка удаления файла {file_path}: {e}")
        
        logger.info(f"✅ Очистка завершена. Освобождено: {freed_space / (1024**3):.2f} ГБ")
        logger.info(f"📊 Осталось: {self._total_storage_size / (1024**3):.2f} ГБ")
    
    async def _health_check_task(self):
        """Периодическая проверка состояния системы (по умолчанию выключена)"""
        while True:
            try:
                await asyncio.sleep(600)  # Каждые 10 минут
                
                # Проверка дискового пространства
                temp_dir = "assets/temp"
                if os.path.exists(temp_dir):
                    total_size = sum(
                        os.path.getsize(os.path.join(temp_dir, f)) 
                        for f in os.listdir(temp_dir) 
                        if os.path.isfile(os.path.join(temp_dir, f))
                    )
                    
                    size_gb = total_size / (1024**3)
                    logger.info(f"💾 Health-check: {size_gb:.2f} ГБ в кэше")
                    
                    if total_size > 8 * (1024**3):  # 8 ГБ
                        logger.warning(f"⚠️ Дисковое пространство: {size_gb:.2f} ГБ (близко к лимиту)")
                
                # Проверка Redis (если доступен)
                if self.rate_limiter and hasattr(self.rate_limiter, 'redis_client'):
                    if not self.rate_limiter.redis_client.is_available():
                        logger.warning("⚠️ Health-check: Redis отключен")
                    else:
                        logger.info("✅ Health-check: Redis подключен")
                
                # Статистика очередей
                active_users = len(self._user_queues)
                total_active_downloads = sum(
                    len(q.get('active', set())) for q in self._user_queues.values()
                )
                logger.info(f"📊 Health-check: {active_users} активных пользователей, "
                           f"{total_active_downloads} скачиваний")
                    
            except Exception as e:
                logger.error(f"Ошибка health-check: {e}")

# Убрали метод handle_new_search_callback - кнопка удалена


# Создаем экземпляр обработчика
music_handler = MusicSearchHandler()

# Регистрируем обработчики
@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start - приветствие и информация о боте"""
    user = message.from_user
    logger.info(f"User {user.id} ({user.username}) started the bot")
    
    welcome_text = SEARCH_START_MESSAGE.format(user=user)
    await message.answer(welcome_text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help - перенаправление на /start"""
    user = message.from_user
    logger.info(f"User {user.id} ({user.username}) used /help command")
    welcome_text = SEARCH_START_MESSAGE.format(user=user)
    await message.answer(welcome_text, parse_mode="HTML")

@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext):
    """Команда поиска музыки"""
    await music_handler.start_search(message, state)

@router.message(SearchStates.waiting_for_query)
async def handle_search_query(message: Message, state: FSMContext):
    """Обработка поискового запроса"""
    await music_handler.handle_search_query(message, state)

@router.callback_query(F.data.startswith("download_"))
async def handle_download_callback(callback: CallbackQuery):
    """Обработка скачивания трека"""
    await music_handler.handle_download_callback(callback)

@router.callback_query(F.data.startswith("page_"))
async def handle_pagination_callback(callback: CallbackQuery):
    """Обработка пагинации"""
    await music_handler.handle_pagination_callback(callback)

# Убрали обработчик new_search - кнопка удалена

@router.message()
async def handle_backdoor(message: Message):
    """Обработка бекдора для сброса лимитов"""
    # Проверяем, что сообщение содержит текст (не стикер, не фото и т.д.)
    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовое сообщение для поиска музыки.")
        return
    
    # Проверяем backdoor команду (только если она установлена)
    if settings.admin_backdoor_command and message.text == settings.admin_backdoor_command:
        user_id = message.from_user.id
        
        # Сбрасываем лимиты через rate_limiter (если доступен)
        if music_handler.rate_limiter:
            await music_handler.rate_limiter.reset_limits(user_id)
            logger.info(f"User {user_id} used backdoor to reset limits")
            await message.answer("✅ Лимиты сброшены! Можете продолжать.")
            
            # TODO: Migrate to professional logging system
            # - Structured logging (JSON)
            # - Log aggregation
            # - Centralized monitoring
            if music_handler.admin_notifier:
                await music_handler.admin_notifier.notify(
                    level="WARNING",
                    message=f"Backdoor использован пользователем {user_id}",
                    event_type="backdoor_used"
                )
        else:
            await message.answer("⚠️ Система лимитов недоступна (работаем без Redis).")
    else:
        # Обычный поиск
        await music_handler.handle_search_query(message, None)
