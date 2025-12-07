"""
Упрощенные настройки бота AMusic (без pydantic)
"""
import os
from dotenv import load_dotenv

# Загружаем .env файл
load_dotenv()


class Settings:
    """Настройки приложения"""
    
    def __init__(self):
        # Telegram Bot
        self.bot_token = os.getenv("BOT_TOKEN", "")
        
        # VK API
        self.vk_access_token = os.getenv("VK_ACCESS_TOKEN", "")
        self.vk_token = os.getenv("VK_TOKEN", "")  # Основной токен VK (legacy)
        
        # Множественные VK токены (через запятую)
        vk_tokens_str = os.getenv("VK_TOKENS", "")
        if vk_tokens_str:
            # Если указаны VK_TOKENS, используем их
            self.vk_tokens = [token.strip() for token in vk_tokens_str.split(",") if token.strip()]
        elif self.vk_token:
            # Fallback на единственный VK_TOKEN
            self.vk_tokens = [self.vk_token]
        else:
            self.vk_tokens = []
        
        self.vk_api_version = os.getenv("VK_API_VERSION", "5.131")
        
        # Database
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///bot.db")
        
        # Redis
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        
        # Settings
        self.max_search_results = int(os.getenv("MAX_SEARCH_RESULTS", "10"))
        self.min_audio_quality = int(os.getenv("MIN_AUDIO_QUALITY", "256"))
        self.cache_ttl = int(os.getenv("CACHE_TTL", "3600"))
        
        # Rate limiting (устаревшие, оставлены для совместимости)
        self.hourly_limit_per_user = int(os.getenv("HOURLY_LIMIT_PER_USER", "10"))
        self.daily_limit_per_user = int(os.getenv("DAILY_LIMIT_PER_USER", "50"))
        
        # Новые лимиты операций (поиск + скачивание)
        self.operations_per_user = int(os.getenv("OPERATIONS_PER_USER", "15"))
        self.user_window_minutes = int(os.getenv("USER_WINDOW_MINUTES", "15"))
        self.global_operations_limit = int(os.getenv("GLOBAL_OPERATIONS_LIMIT", "300"))
        self.global_window_minutes = int(os.getenv("GLOBAL_WINDOW_MINUTES", "30"))
        
        # Кеширование результатов поиска
        self.search_cache_ttl = int(os.getenv("SEARCH_CACHE_TTL", "21600"))  # 6 часов по умолчанию
        
        self.max_file_size_mb = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
        self.max_track_duration_minutes = int(os.getenv("MAX_TRACK_DURATION_MINUTES", "60"))
        self.admin_backdoor_command = os.getenv("ADMIN_BACKDOOR_COMMAND")
        # Если команда не установлена - бот продолжит работу, но функция сброса не будет работать
        if not self.admin_backdoor_command:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("⚠️ ADMIN_BACKDOOR_COMMAND не установлен! Функция сброса лимитов отключена.")
        self.timezone = os.getenv("TIMEZONE", "Europe/Moscow")
        self.max_cached_tracks = int(os.getenv("MAX_CACHED_TRACKS", "200"))
        
        # Telegram уведомления админу
        self.admin_bot_token = os.getenv("ADMIN_BOT_TOKEN")
        self.admin_chat_id = os.getenv("ADMIN_CHAT_ID")
        
        # Логирование
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        # Отправка архивов логов админу
        self.send_log_archives = os.getenv("SEND_LOG_ARCHIVES", "true").lower() in ("true", "1", "yes")
        self.log_archive_interval_hours = int(os.getenv("LOG_ARCHIVE_INTERVAL_HOURS", "6"))
        self.log_archive_max_files = int(os.getenv("LOG_ARCHIVE_MAX_FILES", "6"))
        self.log_archive_max_total_mb = int(os.getenv("LOG_ARCHIVE_MAX_TOTAL_MB", "50"))
        
        # YouTube/SoundCloud через yt-dlp (API ключи опциональны)
        # Используются для извлечения метаданных и скачивания
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY")  # Не требуется для yt-dlp
        self.soundcloud_client_id = os.getenv("SOUNDCLOUD_CLIENT_ID")  # Не требуется для yt-dlp


# Глобальный экземпляр настроек
settings = Settings()