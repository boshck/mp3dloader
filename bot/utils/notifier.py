"""
Модуль уведомлений администратора

TODO: В будущем мигрировать на профессиональную систему логирования:
- Centralized logging (ELK Stack / Loki)
- Structured logging (JSON format)
- Log aggregation для нескольких ботов
- Metrics и alerts (Prometheus + Grafana)
"""

import asyncio
import time
import logging
from typing import Optional
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)


class AdminNotifier:
    """
    Система уведомлений администратора через Telegram
    
    TODO: Migrate to professional logging system
    - Structured logging (JSON)
    - Log aggregation
    - Centralized monitoring
    """
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Инициализация уведомлений
        
        Args:
            bot_token: Токен бота для уведомлений
            chat_id: ID чата администратора
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._bot = None
        
        # Telegram rate limiting
        self._last_sent = {}  # {event_type: timestamp}
        self._min_interval = 1.0  # 1 секунда между сообщениями (Telegram limit)
        self._max_messages_per_second = 30  # Telegram API limit
    
    async def _get_bot(self) -> Bot:
        """Получить или создать Bot instance"""
        if self._bot is None:
            self._bot = Bot(
                token=self.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            )
        return self._bot
    
    def _check_rate_limit(self, event_type: str, min_interval: int = 3600) -> bool:
        """
        Проверка rate limiting по типу события
        
        Args:
            event_type: Тип события для группировки
            min_interval: Минимальный интервал в секундах (по умолчанию 1 час)
            
        Returns:
            True если можно отправить
        """
        current_time = time.time()
        last_sent = self._last_sent.get(event_type, 0)
        
        # Разные интервалы для разных типов событий
        if event_type == "redis_error":
            min_interval = 3600  # 1 час для Redis
        elif event_type in ["search_error", "download_error"]:
            min_interval = 300  # 5 минут для ошибок операций
        elif event_type == "backdoor_used":
            min_interval = 1  # Без throttling
        else:
            min_interval = 60  # 1 минута по умолчанию
        
        if current_time - last_sent < min_interval:
            return False
        
        self._last_sent[event_type] = current_time
        return True
    
    async def notify(self, level: str, message: str, event_type: str = "general") -> bool:
        """
        Отправить уведомление админу
        
        Args:
            level: Уровень критичности ("CRITICAL", "ERROR", "WARNING", "INFO")
            message: Текст сообщения
            event_type: Тип события для rate limiting
            
        Returns:
            True если уведомление отправлено, False если пропущено из-за rate limiting
        """
        try:
            # Проверяем rate limiting
            if not self._check_rate_limit(event_type):
                logger.debug(f"Rate limit exceeded for {event_type}, skipping notification")
                return False
            
            # Получаем бота
            bot = await self._get_bot()
            
            # Форматируем сообщение
            emoji_map = {
                "CRITICAL": "🚨",
                "ERROR": "❌", 
                "WARNING": "⚠️",
                "INFO": "ℹ️"
            }
            
            emoji = emoji_map.get(level, "📢")
            formatted_message = f"{emoji} <b>AMusic Bot Alert</b>\n\n"
            formatted_message += f"<b>Level:</b> {level}\n"
            formatted_message += f"<b>Event:</b> {event_type}\n"
            formatted_message += f"<b>Message:</b> {message}\n"
            formatted_message += f"<b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            # Отправляем уведомление
            await bot.send_message(
                chat_id=self.chat_id,
                text=formatted_message,
                parse_mode="HTML"
            )
            
            logger.info(f"Admin notification sent: {level} - {event_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")
            return False
    
    async def close(self):
        """Закрыть соединения"""
        if self._bot:
            await self._bot.session.close()
            self._bot = None


# Глобальный экземпляр (будет инициализирован в main.py)
admin_notifier: Optional[AdminNotifier] = None


async def notify_admin(level: str, message: str, event_type: str = "general") -> bool:
    """
    Глобальная функция для отправки уведомлений админу
    
    Args:
        level: Уровень критичности
        message: Текст сообщения  
        event_type: Тип события
        
    Returns:
        True если уведомление отправлено
    """
    global admin_notifier
    
    if admin_notifier is None:
        logger.warning("Admin notifier not initialized")
        return False
    
    return await admin_notifier.notify(level, message, event_type)
