"""
AMusic Bot - Telegram Music Bot
Простая версия без лишних зависимостей
"""
import asyncio
import sys
import os
from pathlib import Path
import logging
from logging.handlers import TimedRotatingFileHandler

# Кодировка будет исправлена автоматически

# Добавляем корневую папку в путь для импортов
sys.path.append(str(Path(__file__).parent))

# Загружаем .env файл
from dotenv import load_dotenv
load_dotenv()

# Импортируем настройки для LOG_LEVEL
from bot.config.settings import settings


# Настройка глобального логирования
def setup_logging():
    """Настройка системы логирования"""
    # Создаём папку для логов
    os.makedirs("logs", exist_ok=True)
    
    # Определяем уровень логирования
    log_level = getattr(logging, settings.log_level, logging.INFO)
    
    # Формат логов
    log_format = '%(asctime)s %(levelname)s %(name)s %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Консольный handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Файловый handler с ротацией по часу
    file_handler = TimedRotatingFileHandler(
        filename='logs/bot.log',
        when='H',
        interval=1,
        backupCount=72,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Понижаем уровень для aiogram (слишком много логов)
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    
    logging.info(f"Logging initialized: level={settings.log_level}")


# Вызываем перед всем остальным
setup_logging()
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# Импортируем обработчики
logger.info("Импортируем обработчики...")
try:
    from bot.handlers.start import router as start_router
    logger.info("start_router импортирован")
except ImportError as e:
    logger.error(f"Ошибка импорта start_router: {e}")
    start_router = None

try:
    from bot.handlers.help import router as help_router
    logger.info("help_router импортирован")
except ImportError as e:
    logger.error(f"Ошибка импорта help_router: {e}")
    help_router = None

# try:
#     from bot.handlers.youtube import router as youtube_router
#     logger.info("youtube_router импортирован")
# except ImportError as e:
#     logger.error(f"Ошибка импорта youtube_router: {e}")
#     youtube_router = None
logger.info("youtube_router отключен (DEPRECATED - функции в music_search_router)")
youtube_router = None

try:
    from bot.handlers.music_search import router as music_search_router
    logger.info("music_search_router импортирован")
except ImportError as e:
    logger.error(f"Ошибка импорта music_search_router: {e}")
    music_search_router = None

logger.info("Импорт обработчиков завершен!")

# Импортируем Redis и Rate Limiter
try:
    from bot.utils.redis_client import RedisClient
    from bot.utils.rate_limiter import RateLimiter
except ImportError as e:
    logger.error(f"Ошибка импорта Redis/RateLimiter: {e}")
    RedisClient = None
    RateLimiter = None


async def main():
    """Основная функция запуска бота"""
    redis_client = None  # Инициализируем в начале для доступа в finally
    log_cleanup_task = None
    log_sender_task_ref = None
    
    try:
        # Импортируем настройки в начале
        from bot.config.settings import settings
        
        logger.info("Проверяем переменные окружения...")
        
        # Получаем токен из переменных окружения
        BOT_TOKEN = os.getenv("BOT_TOKEN")
        if not BOT_TOKEN:
            logger.error("ERROR: BOT_TOKEN not found in .env file!")
            return
        
        logger.info("Starting AMusic Bot...")
        logger.info(f"Bot token: {BOT_TOKEN[:10]}...")
        logger.info(f"VK tokens: {len(settings.vk_tokens)} configured")
        logger.info("Sources: VK, YouTube (via yt-dlp), SoundCloud (via yt-dlp)")
        
        logger.info("Инициализируем бота...")
        
        # Инициализация бота
        logger.info("Создаем Bot объект...")
        bot = Bot(
            token=BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logger.info("Bot объект создан")
        
        # Инициализация диспетчера
        logger.info("Создаем Dispatcher...")
        dp = Dispatcher(storage=MemoryStorage())
        logger.info("Dispatcher создан")
        
        # Инициализируем Redis и Rate Limiter
        logger.info("Инициализируем Redis и Rate Limiter...")
        rate_limiter = None
        usage_stats = None
        
        # Инициализируем систему уведомлений администратора
        logger.info("Инициализируем систему уведомлений...")
        admin_notifier = None
        if settings.admin_bot_token and settings.admin_chat_id:
            from bot.utils.notifier import AdminNotifier
            admin_notifier = AdminNotifier(
                bot_token=settings.admin_bot_token,
                chat_id=settings.admin_chat_id
            )
            logger.info("Система уведомлений инициализирована")
        else:
            logger.warning("ADMIN_BOT_TOKEN или ADMIN_CHAT_ID не установлены, уведомления отключены")
        
        if RedisClient and RateLimiter:
            # Создаем Redis клиент
            redis_client = RedisClient(redis_url=settings.redis_url)
            redis_client.set_admin_notifier(admin_notifier)
            
            try:
                await redis_client.connect()
                logger.info("Redis подключен успешно")
                
                # Устанавливаем начальное состояние
                redis_client._last_state = redis_client.is_available()
                
                # Если Redis недоступен при старте - уведомление
                if not redis_client.is_available() and admin_notifier:
                    await admin_notifier.notify(
                        level="CRITICAL",
                        message="Redis unavailable at startup",
                        event_type="redis_error"
                    )
                
                # Запускаем мониторинг
                await redis_client.start_monitoring()
                logger.info("Redis monitoring started")
                
                if redis_client.is_available():
                    logger.info(f"Redis подключён: {settings.redis_url}")
                    # Тестируем запись/чтение
                    await redis_client.set("test_key", "test_value", ex=10)
                    test_value = await redis_client.get("test_key")
                    if test_value == "test_value":
                        logger.info("Redis тест прошел успешно")
                    else:
                        logger.warning("Redis тест не прошел, но подключение работает")
                else:
                    logger.warning("Redis недоступен, работаем в in-memory режиме")
                    logger.info(f"URL: {settings.redis_url}")
                    logger.info("Проверьте, что Memurai запущен и доступен на localhost:6379")
            except Exception as e:
                logger.error(f"Redis недоступен: {e}")
                logger.warning("RateLimiter переведен в in-memory режим")
                
                # TODO: Migrate to professional logging system
                # - Structured logging (JSON)
                # - Log aggregation
                # - Centralized monitoring
                if admin_notifier:
                    await admin_notifier.notify(
                        level="CRITICAL",
                        message=f"Redis недоступен: {e}",
                        event_type="redis_error"
                    )
            
            # Создаем Rate Limiter
            rate_limiter = RateLimiter(
                redis_client=redis_client,
                user_operations_limit=settings.operations_per_user,
                user_window_minutes=settings.user_window_minutes,
                global_operations_limit=settings.global_operations_limit,
                global_window_minutes=settings.global_window_minutes,
                search_cache_ttl=settings.search_cache_ttl
            )
            logger.info("RateLimiter создан")
            logger.info(f"Лимиты: {settings.operations_per_user} операций/{settings.user_window_minutes} мин на пользователя")
            logger.info(f"Глобально: {settings.global_operations_limit} операций/{settings.global_window_minutes} мин")

            # Сервис статистики использования (админ-команда /admin_stats)
            from bot.utils.usage_stats import UsageStatsService
            usage_stats = UsageStatsService(redis_client=redis_client)
            logger.info("UsageStatsService инициализирован")
        else:
            logger.warning("Redis/RateLimiter не импортированы, работаем без лимитов")
        
        # Регистрируем роутеры
        logger.info("Регистрируем роутеры...")
        
        # DEPRECATED: Отключаем старые роутеры, все команды теперь в music_search_router
        # if start_router:
        #     dp.include_router(start_router)
        #     logger.info("start_router зарегистрирован")
        # else:
        #     logger.error("start_router не зарегистрирован (ошибка импорта)")
        logger.info("start_router отключен (DEPRECATED - команды в music_search_router)")
            
        # if help_router:
        #     dp.include_router(help_router)
        #     logger.info("help_router зарегистрирован")
        # else:
        #     logger.error("help_router не зарегистрирован (ошибка импорта)")
        logger.info("help_router отключен (DEPRECATED - команды в music_search_router)")
            
        # if youtube_router:
        #     dp.include_router(youtube_router)
        #     logger.info("youtube_router зарегистрирован")
        # else:
        #     logger.error("youtube_router не зарегистрирован (ошибка импорта)")
        logger.info("youtube_router отключен (DEPRECATED - функции в music_search_router)")
            
        if music_search_router:
            # Передаем зависимости в music_handler
            from bot.handlers.music_search import music_handler
            music_handler.rate_limiter = rate_limiter
            music_handler.admin_notifier = admin_notifier
            music_handler.usage_stats = usage_stats
            logger.info("RateLimiter, AdminNotifier и UsageStats переданы в music_handler")
            
            dp.include_router(music_search_router)
            logger.info("music_search_router зарегистрирован")
        else:
            logger.error("music_search_router не зарегистрирован (ошибка импорта)")
            
        logger.info("Регистрация роутеров завершена!")
        logger.info("Режим работы: скачивание по ссылкам YouTube/SoundCloud")
        logger.info("Доступны команды: /start, /help, /search, /admin_stats")
        logger.info("Все старые роутеры отключены (DEPRECATED)")
        
        # Запускаем фоновые задачи управления ресурсами
        if music_search_router:
            from bot.handlers.music_search import music_handler
            await music_handler.start_background_tasks()
            logger.info("Фоновые задачи управления ресурсами запущены")
        
        # Запускаем фоновую очистку логов (раз в час)
        from bot.utils.log_cleanup import cleanup_old_logs
        
        async def cleanup_task_func():
            while True:
                try:
                    await asyncio.sleep(3600)  # Каждый час
                    await cleanup_old_logs(
                        logs_dir="logs",
                        age_threshold_hours=48,
                        check_threshold_hours=72
                    )
                except Exception as e:
                    logger.error(f"Log cleanup error: {e}")
        
        log_cleanup_task = asyncio.create_task(cleanup_task_func())
        logger.info("Log cleanup task started")
        
        # Запускаем фоновую отправку логов админу (если включено)
        if settings.send_log_archives and admin_notifier:
            from bot.utils.log_cleanup import periodic_log_sender
            
            async def sender_task_func():
                while True:
                    try:
                        await periodic_log_sender(
                            logs_dir="logs",
                            admin_notifier=admin_notifier,
                            interval_hours=settings.log_archive_interval_hours,
                            files_per_batch=settings.log_archive_max_files,
                            max_total_mb=settings.log_archive_max_total_mb
                        )
                    except Exception as e:
                        logger.error(f"Log sender error: {e}")
            
            log_sender_task_ref = asyncio.create_task(sender_task_func())
            logger.info(
                f"Log sender task started (every {settings.log_archive_interval_hours}h, "
                f"max {settings.log_archive_max_files} files, max {settings.log_archive_max_total_mb} MB)"
            )
        else:
            if not settings.send_log_archives:
                logger.info("Log sender task disabled (SEND_LOG_ARCHIVES=false)")
            elif not admin_notifier:
                logger.warning("Log sender task disabled (admin_notifier not available)")
        
        logger.info("Bot initialized successfully")
        logger.info("Bot is running... Press Ctrl+C to stop")
        
        # Запускаем бота
        logger.info("Запускаем polling...")
        await dp.start_polling(bot)
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        sys.exit(1)
    finally:
        # Graceful shutdown - закрытие всех сессий
        logger.info("Graceful shutdown...")
        
        # Отменяем фоновые задачи
        try:
            if log_cleanup_task and not log_cleanup_task.done():
                log_cleanup_task.cancel()
                try:
                    await log_cleanup_task
                except asyncio.CancelledError:
                    logger.info("Log cleanup task cancelled")
        except Exception as e:
            logger.warning(f"Error cancelling log cleanup task: {e}")
        
        try:
            if log_sender_task_ref and not log_sender_task_ref.done():
                log_sender_task_ref.cancel()
                try:
                    await log_sender_task_ref
                except asyncio.CancelledError:
                    logger.info("Log sender task cancelled")
        except Exception as e:
            logger.warning(f"Error cancelling log sender task: {e}")
        
        try:
            # Закрываем Redis
            if redis_client:
                await redis_client.close()
                logger.info("Redis connection closed")
        except Exception as e:
            logger.warning(f"Redis cleanup error: {e}")
        
        try:
            if music_search_router:
                from bot.handlers.music_search import music_handler
                await music_handler.cleanup()
                logger.info("Music handler cleanup completed")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")
        
        try:
            # Закрываем admin notifier
            if admin_notifier:
                await admin_notifier.close()
                logger.info("Admin notifier closed")
        except Exception as e:
            logger.warning(f"Admin notifier cleanup error: {e}")
            
        logger.info("Shutdown completed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
