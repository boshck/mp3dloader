"""
Redis клиент с автоматическим fallback на in-memory режим
"""
import logging
import time
import json
from typing import Optional, Any, Dict, List
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis клиент с fallback на in-memory при недоступности Redis"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis = None
        self._connected = False
        self._fallback_warned = False
        
        # In-memory хранилище для fallback режима
        self._memory_store: Dict[str, Any] = {}
        self._memory_zsets: Dict[str, List[tuple]] = defaultdict(list)  # {key: [(score, value), ...]}
        self._memory_ttl: Dict[str, float] = {}  # {key: expire_timestamp}
        
        # Мониторинг состояния Redis
        self._monitoring_task = None
        self._admin_notifier = None
        self._last_state = None  # True=connected, False=disconnected, None=unknown
        
        # Счетчики для повторных уведомлений
        self._offline_start_time = None
        self._last_hourly_notification = None
    
    async def connect(self):
        """Попытка подключения к Redis"""
        try:
            import redis.asyncio as aioredis
            
            self._redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2
            )
            
            # Проверяем подключение
            await self._redis.ping()
            self._connected = True
            logger.info(f"✅ Redis connected: {self.redis_url}")
            
        except ImportError:
            if not self._fallback_warned:
                logger.warning("⚠️ redis[async] не установлен, работаем в in-memory режиме")
                self._fallback_warned = True
            self._connected = False
            
        except Exception as e:
            if not self._fallback_warned:
                logger.warning(f"⚠️ Redis недоступен ({e}), работаем в in-memory режиме")
                self._fallback_warned = True
            self._connected = False
    
    def is_available(self) -> bool:
        """Проверяет доступность Redis"""
        return self._connected
    
    def set_admin_notifier(self, notifier):
        """Установить notifier для уведомлений"""
        self._admin_notifier = notifier
    
    async def start_monitoring(self):
        """
        Запускает мониторинг состояния Redis с:
        - Проверкой каждую минуту
        - Попыткой переподключения каждую минуту при offline
        - Уведомлением админу каждый час пока offline
        """
        import asyncio
        
        async def monitor():
            while True:
                try:
                    await asyncio.sleep(60)  # Проверка каждую минуту
                    
                    current_state = self.is_available()
                    
                    # Если Redis offline
                    if not current_state:
                        # Запоминаем время первого падения
                        if self._offline_start_time is None:
                            self._offline_start_time = time.time()
                            logger.error("Redis went offline")
                            
                            # Первое уведомление сразу
                            if self._admin_notifier:
                                await self._admin_notifier.notify(
                                    level="CRITICAL",
                                    message="Redis connection lost",
                                    event_type="redis_error"
                                )
                                self._last_hourly_notification = time.time()
                        
                        else:
                            # Проверяем, прошел ли час с последнего уведомления
                            current_time = time.time()
                            offline_duration = current_time - self._offline_start_time
                            
                            if (self._last_hourly_notification is None or 
                                current_time - self._last_hourly_notification >= 3600):
                                
                                # Отправляем повторное уведомление
                                if self._admin_notifier:
                                    hours_offline = int(offline_duration / 3600)
                                    await self._admin_notifier.notify(
                                        level="CRITICAL",
                                        message=f"Redis still offline (duration: {hours_offline}h)",
                                        event_type="redis_error"
                                    )
                                    self._last_hourly_notification = current_time
                                    logger.warning(f"Redis offline for {hours_offline}h, notification sent")
                        
                        # Пытаемся переподключиться
                        logger.info("Attempting to reconnect to Redis...")
                        await self.connect()
                        
                        # Проверяем результат переподключения
                        if self.is_available():
                            logger.info("Redis reconnection successful!")
                            # Состояние изменится на следующей итерации
                    
                    # Если Redis online и был offline
                    elif current_state and self._offline_start_time is not None:
                        # Redis восстановился
                        offline_duration = time.time() - self._offline_start_time
                        hours_offline = offline_duration / 3600
                        
                        logger.info(f"Redis connection restored after {hours_offline:.1f}h")
                        
                        if self._admin_notifier:
                            await self._admin_notifier.notify(
                                level="INFO",
                                message=f"Redis connection restored (was offline: {hours_offline:.1f}h)",
                                event_type="redis_restored"
                            )
                        
                        # Сбрасываем счетчики
                        self._offline_start_time = None
                        self._last_hourly_notification = None
                    
                    self._last_state = current_state
                    
                except Exception as e:
                    logger.error(f"Redis monitoring error: {e}")
        
        self._monitoring_task = asyncio.create_task(monitor())
        logger.info("Redis monitoring started (check every 60s, reconnect on failure)")
    
    async def get(self, key: str) -> Optional[str]:
        """Получить значение по ключу"""
        if self._connected and self._redis:
            try:
                return await self._redis.get(key)
            except Exception as e:
                logger.warning(f"Redis GET error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        self._clean_expired()
        return self._memory_store.get(key)
    
    async def set(self, key: str, value: str, ex: Optional[int] = None):
        """Установить значение с опциональным TTL (в секундах)"""
        if self._connected and self._redis:
            try:
                await self._redis.set(key, value, ex=ex)
                return
            except Exception as e:
                logger.warning(f"Redis SET error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        self._memory_store[key] = value
        if ex:
            self._memory_ttl[key] = time.time() + ex
    
    async def delete(self, *keys: str):
        """Удалить ключи"""
        if self._connected and self._redis:
            try:
                await self._redis.delete(*keys)
                return
            except Exception as e:
                logger.warning(f"Redis DELETE error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        for key in keys:
            self._memory_store.pop(key, None)
            self._memory_zsets.pop(key, None)
            self._memory_ttl.pop(key, None)
    
    async def zadd(self, key: str, mapping: Dict[str, float], nx: bool = False):
        """Добавить элементы в sorted set"""
        if self._connected and self._redis:
            try:
                await self._redis.zadd(key, mapping, nx=nx)
                return
            except Exception as e:
                logger.warning(f"Redis ZADD error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        if key not in self._memory_zsets:
            self._memory_zsets[key] = []
        
        for value, score in mapping.items():
            # Если nx=True, добавляем только если элемента нет
            if nx:
                if not any(v == value for s, v in self._memory_zsets[key]):
                    self._memory_zsets[key].append((score, value))
            else:
                # Обновляем существующий или добавляем новый
                existing = False
                for i, (s, v) in enumerate(self._memory_zsets[key]):
                    if v == value:
                        self._memory_zsets[key][i] = (score, value)
                        existing = True
                        break
                if not existing:
                    self._memory_zsets[key].append((score, value))
        
        # Сортируем по score
        self._memory_zsets[key].sort(key=lambda x: x[0])
    
    async def zremrangebyscore(self, key: str, min_score: float, max_score: float):
        """Удалить элементы из sorted set по диапазону score"""
        if self._connected and self._redis:
            try:
                await self._redis.zremrangebyscore(key, min_score, max_score)
                return
            except Exception as e:
                logger.warning(f"Redis ZREMRANGEBYSCORE error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        if key in self._memory_zsets:
            self._memory_zsets[key] = [
                (score, value) for score, value in self._memory_zsets[key]
                if not (min_score <= score <= max_score)
            ]
    
    async def zcard(self, key: str) -> int:
        """Получить количество элементов в sorted set"""
        if self._connected and self._redis:
            try:
                return await self._redis.zcard(key)
            except Exception as e:
                logger.warning(f"Redis ZCARD error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        return len(self._memory_zsets.get(key, []))
    
    async def exists(self, key: str) -> bool:
        """Проверить существование ключа"""
        if self._connected and self._redis:
            try:
                return await self._redis.exists(key) > 0
            except Exception as e:
                logger.warning(f"Redis EXISTS error, fallback to memory: {e}")
                self._connected = False
        
        # In-memory fallback
        self._clean_expired()
        return key in self._memory_store or key in self._memory_zsets
    
    def _clean_expired(self):
        """Очистка истекших ключей в in-memory режиме"""
        current_time = time.time()
        expired_keys = [
            key for key, expire_time in self._memory_ttl.items()
            if current_time > expire_time
        ]
        for key in expired_keys:
            self._memory_store.pop(key, None)
            self._memory_ttl.pop(key, None)
    
    async def close(self):
        """Закрыть соединение с Redis"""
        if self._redis and self._connected:
            try:
                await self._redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")
        
        # Очищаем in-memory хранилище
        self._memory_store.clear()
        self._memory_zsets.clear()
        self._memory_ttl.clear()


# Глобальный экземпляр клиента (будет инициализирован в main.py)
redis_client: Optional[RedisClient] = None

