"""
Сервис лимитов операций с использованием Redis
"""
import time
import json
import logging
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger(__name__)


class RateLimiter:
    """Сервис для контроля лимитов операций и кеширования результатов"""
    
    def __init__(
        self,
        redis_client,
        user_operations_limit: int = 15,
        user_window_minutes: int = 15,
        global_operations_limit: int = 300,
        global_window_minutes: int = 30,
        search_cache_ttl: int = 21600  # 6 часов
    ):
        """
        Инициализация сервиса лимитов
        
        Args:
            redis_client: Экземпляр RedisClient
            user_operations_limit: Максимум операций на пользователя
            user_window_minutes: Окно времени для пользователя (минуты)
            global_operations_limit: Максимум операций глобально
            global_window_minutes: Окно времени глобально (минуты)
            search_cache_ttl: TTL кеша поиска (секунды)
        """
        self.redis = redis_client
        self.user_limit = user_operations_limit
        self.user_window_seconds = user_window_minutes * 60
        self.global_limit = global_operations_limit
        self.global_window_seconds = global_window_minutes * 60
        self.search_cache_ttl = search_cache_ttl
        
        logger.info(
            f"RateLimiter initialized: "
            f"user={user_operations_limit}/{user_window_minutes}m, "
            f"global={global_operations_limit}/{global_window_minutes}m"
        )
    
    async def check_limit(self, user_id: int) -> Tuple[bool, int, str]:
        """
        Проверить лимиты для пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Tuple[allowed, wait_minutes, message]
            - allowed: True если операция разрешена
            - wait_minutes: Сколько минут ждать (если не разрешено)
            - message: Сообщение для пользователя
        """
        current_time = time.time()
        
        # Проверяем глобальный лимит
        global_key = "global:operations"
        await self._cleanup_old_operations(global_key, self.global_window_seconds, current_time)
        
        global_count = await self.redis.zcard(global_key)
        if global_count >= self.global_limit:
            wait_seconds = await self._get_wait_time(global_key, self.global_window_seconds, current_time)
            wait_minutes = max(1, int(wait_seconds / 60))
            message = f"🎵 Сервер перегружен! Пожалуйста, подождите ещё {wait_minutes} минут перед следующей операцией 🙏"
            logger.warning(f"Global limit exceeded: {global_count}/{self.global_limit}")
            return False, wait_minutes, message
        
        # Проверяем лимит пользователя
        user_key = f"user:{user_id}:operations"
        await self._cleanup_old_operations(user_key, self.user_window_seconds, current_time)
        
        user_count = await self.redis.zcard(user_key)
        if user_count >= self.user_limit:
            wait_seconds = await self._get_wait_time(user_key, self.user_window_seconds, current_time)
            wait_minutes = max(1, int(wait_seconds / 60))
            message = f"🎵 Вы очень активны! Пожалуйста, подождите ещё {wait_minutes} минут перед следующей операцией 🙏"
            logger.warning(f"User {user_id} limit exceeded: {user_count}/{self.user_limit}")
            return False, wait_minutes, message
        
        # Лимиты не превышены
        return True, 0, ""
    
    async def register_operation(self, user_id: int):
        """
        Зарегистрировать операцию для пользователя
        
        Args:
            user_id: ID пользователя
        """
        current_time = time.time()
        operation_id = f"{current_time}:{user_id}"
        
        # Добавляем в пользовательский счётчик
        user_key = f"user:{user_id}:operations"
        await self.redis.zadd(user_key, {operation_id: current_time})
        
        # Добавляем в глобальный счётчик
        global_key = "global:operations"
        await self.redis.zadd(global_key, {operation_id: current_time})
        
        # Получаем текущие счётчики для логирования
        user_count = await self.redis.zcard(user_key)
        global_count = await self.redis.zcard(global_key)
        
        logger.info(
            f"Operation registered for user {user_id}: "
            f"user={user_count}/{self.user_limit}, "
            f"global={global_count}/{self.global_limit}"
        )
    
    async def reset_limits(self, user_id: Optional[int] = None):
        """
        Сбросить лимиты
        
        Args:
            user_id: ID пользователя (если None - сбрасывает все лимиты)
        """
        if user_id is not None:
            # Сбрасываем только для конкретного пользователя
            user_key = f"user:{user_id}:operations"
            await self.redis.delete(user_key)
            logger.info(f"Limits reset for user {user_id}")
        else:
            # Сбрасываем всё (для суперадминов)
            await self.redis.delete("global:operations")
            # Удаляем все пользовательские ключи (в production это может быть медленно)
            logger.info("All limits reset")
    
    async def get_cached_search(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Получить результаты поиска из кеша
        
        Args:
            query: Поисковый запрос
            
        Returns:
            Список треков или None если кеша нет
        """
        cache_key = f"search:{query.lower()}"
        cached_data = await self.redis.get(cache_key)
        
        if cached_data:
            try:
                results = json.loads(cached_data)
                logger.info(f"Cache HIT for query: '{query}' ({len(results)} results)")
                return results
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to decode cached search results: {e}")
                return None
        
        logger.info(f"Cache MISS for query: '{query}'")
        return None
    
    async def cache_search(self, query: str, results: List[Dict[str, Any]]):
        """
        Кешировать результаты поиска
        
        Args:
            query: Поисковый запрос
            results: Список треков для кеширования
        """
        cache_key = f"search:{query.lower()}"
        
        try:
            # Сериализуем только необходимые данные (без объектов Track)
            serializable_results = []
            for track_info in results:
                track = track_info['track']
                serializable_track = {
                    'id': track.id,
                    'title': track.title,
                    'artist': track.artist,
                    'duration': track.duration,
                    'source': track_info['source'],
                    'display_name': track_info['display_name'],
                    'info_text': track_info['info_text']
                }
                
                # Добавляем специфичные поля в зависимости от источника
                if track_info['source'] == 'vk':
                    serializable_track['owner_id'] = track.owner_id
                elif track_info['source'] == 'youtube':
                    # У YouTubeTrack есть: id, channel, webpage_url, filesize
                    serializable_track['channel'] = track.channel
                    serializable_track['webpage_url'] = getattr(track, 'webpage_url', '')
                    if hasattr(track, 'filesize') and track.filesize:
                        serializable_track['filesize'] = track.filesize
                elif track_info['source'] == 'soundcloud':
                    # У SoundCloudTrack есть: id, artist, permalink_url, filesize
                    serializable_track['permalink_url'] = getattr(track, 'permalink_url', '')
                    if hasattr(track, 'filesize') and track.filesize:
                        serializable_track['filesize'] = track.filesize
                
                serializable_results.append(serializable_track)
            
            cached_data = json.dumps(serializable_results, ensure_ascii=False)
            await self.redis.set(cache_key, cached_data, ex=self.search_cache_ttl)
            
            logger.info(f"Cached search results for '{query}': {len(results)} tracks, TTL={self.search_cache_ttl}s")
            
        except Exception as e:
            logger.warning(f"Failed to cache search results: {e}")
    
    async def _cleanup_old_operations(self, key: str, window_seconds: int, current_time: float):
        """
        Удалить старые операции за пределами временного окна
        
        Args:
            key: Ключ Redis
            window_seconds: Размер окна в секундах
            current_time: Текущее время (Unix timestamp)
        """
        cutoff_time = current_time - window_seconds
        await self.redis.zremrangebyscore(key, 0, cutoff_time)
    
    async def _get_wait_time(self, key: str, window_seconds: int, current_time: float) -> int:
        """
        Получить время ожидания до следующей доступной операции
        
        Args:
            key: Ключ Redis
            window_seconds: Размер окна в секундах
            current_time: Текущее время (Unix timestamp)
            
        Returns:
            Время ожидания в секундах
        """
        # Получаем самую старую операцию в окне
        if self.redis.is_available() and self.redis._redis:
            try:
                oldest = await self.redis._redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    wait_time = int((oldest_time + window_seconds) - current_time)
                    return max(0, wait_time)
            except Exception as e:
                logger.warning(f"Error getting wait time: {e}")
        else:
            # In-memory fallback
            if key in self.redis._memory_zsets and self.redis._memory_zsets[key]:
                oldest_time = self.redis._memory_zsets[key][0][0]
                wait_time = int((oldest_time + window_seconds) - current_time)
                return max(0, wait_time)
        
        return 0
    
    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Получить статистику по лимитам пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Словарь со статистикой
        """
        current_time = time.time()
        
        user_key = f"user:{user_id}:operations"
        await self._cleanup_old_operations(user_key, self.user_window_seconds, current_time)
        user_count = await self.redis.zcard(user_key)
        
        global_key = "global:operations"
        await self._cleanup_old_operations(global_key, self.global_window_seconds, current_time)
        global_count = await self.redis.zcard(global_key)
        
        return {
            'user_operations': user_count,
            'user_limit': self.user_limit,
            'user_window_minutes': self.user_window_seconds // 60,
            'global_operations': global_count,
            'global_limit': self.global_limit,
            'global_window_minutes': self.global_window_seconds // 60
        }

