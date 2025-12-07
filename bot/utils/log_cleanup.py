"""
Модуль очистки старых лог-файлов с отправкой архивов администратору
"""
import os
import time
import logging
import zipfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


async def send_logs_to_admin(
    log_files: List[str],
    admin_notifier,
    logs_dir: str = "logs"
):
    """
    Отправляет лог-файлы админу в ZIP-архиве
    
    Args:
        log_files: Список путей к лог-файлам
        admin_notifier: AdminNotifier instance
        logs_dir: Директория с логами
    """
    if not log_files or not admin_notifier:
        return
    
    try:
        # Создаем временный ZIP архив
        archive_name = f"logs_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        archive_path = os.path.join(logs_dir, archive_name)
        
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for log_file in log_files:
                if os.path.exists(log_file):
                    zipf.write(log_file, os.path.basename(log_file))
        
        logger.info(f"Created log archive: {archive_name} ({len(log_files)} files)")
        
        # Отправляем архив админу
        from aiogram.types import FSInputFile
        bot = await admin_notifier._get_bot()
        
        document = FSInputFile(archive_path)
        await bot.send_document(
            chat_id=admin_notifier.chat_id,
            document=document,
            caption=f"📋 Log archive: {len(log_files)} files"
        )
        
        logger.info(f"Log archive sent to admin: {archive_name}")
        
        # Удаляем временный архив
        os.remove(archive_path)
        
    except Exception as e:
        logger.error(f"Failed to send logs to admin: {e}")


async def cleanup_old_logs(
    logs_dir: str = "logs",
    age_threshold_hours: int = 48,
    check_threshold_hours: int = 72
):
    """
    Удаляет старые лог-файлы по новой логике:
    - Если есть файлы старше 72ч → удалить ВСЕ файлы старше 48ч
    
    Args:
        logs_dir: Папка с логами
        age_threshold_hours: Удалить файлы старше (часов)
        check_threshold_hours: Триггер - проверять файлы старше (часов)
    """
    if not os.path.exists(logs_dir):
        return
    
    current_time = time.time()
    age_threshold = age_threshold_hours * 3600
    check_threshold = check_threshold_hours * 3600
    
    # Собираем информацию о всех файлах
    all_files = []
    for filename in os.listdir(logs_dir):
        file_path = os.path.join(logs_dir, filename)
        
        if not os.path.isfile(file_path):
            continue
        
        # Пропускаем временные архивы
        if filename.startswith("logs_") and filename.endswith(".zip"):
            continue
        
        file_age = current_time - os.path.getmtime(file_path)
        all_files.append((file_path, file_age, filename))
    
    # Проверяем, есть ли файлы старше check_threshold (72ч)
    has_very_old_files = any(file_age > check_threshold for _, file_age, _ in all_files)
    
    if not has_very_old_files:
        logger.debug("No files older than 72h, cleanup skipped")
        return
    
    logger.info(f"Found files older than {check_threshold_hours}h, triggering cleanup")
    
    # Удаляем ВСЕ файлы старше age_threshold (48ч)
    deleted_count = 0
    for file_path, file_age, filename in all_files:
        if file_age > age_threshold:
            try:
                os.remove(file_path)
                deleted_count += 1
                logger.info(f"Deleted old log: {filename} (age: {file_age/3600:.1f}h)")
            except Exception as e:
                logger.error(f"Failed to delete {filename}: {e}")
    
    if deleted_count > 0:
        logger.info(f"Cleanup completed: {deleted_count} log files deleted")


# Глобальная задача для периодической отправки логов админу
_last_log_send_time = None


async def periodic_log_sender(
    logs_dir: str = "logs",
    admin_notifier = None,
    interval_hours: int = 6,
    files_per_batch: int = 6,
    max_total_mb: int = 50
):
    """
    Периодически отправляет лог-файлы админу с контролем размера
    Запускается как фоновая задача
    
    Args:
        logs_dir: Директория с логами
        admin_notifier: AdminNotifier instance
        interval_hours: Интервал отправки (часов)
        files_per_batch: Максимальное количество файлов в архиве
        max_total_mb: Максимальный суммарный размер файлов (МБ)
    """
    import asyncio
    global _last_log_send_time
    
    while True:
        try:
            await asyncio.sleep(interval_hours * 3600)
            
            if not admin_notifier:
                logger.warning("Admin notifier not available, skipping log send")
                continue
            
            if not os.path.exists(logs_dir):
                continue
            
            # Собираем все файлы с их размерами
            log_files = []
            for filename in os.listdir(logs_dir):
                file_path = os.path.join(logs_dir, filename)
                
                if not os.path.isfile(file_path):
                    continue
                
                # Пропускаем активный лог и временные архивы
                if filename == "bot.log" or (filename.startswith("logs_") and filename.endswith(".zip")):
                    continue
                
                mtime = os.path.getmtime(file_path)
                size_bytes = os.path.getsize(file_path)
                log_files.append((file_path, mtime, filename, size_bytes))
            
            # Сортируем по времени модификации (старые первые, чтобы не рвалась история)
            log_files.sort(key=lambda x: x[1])
            
            # Накапливаем файлы до достижения лимита
            max_size_bytes = max_total_mb * 1024 * 1024
            selected_files = []
            total_size = 0
            size_limit_hit = False  # Флаг превышения лимита по размеру
            
            for file_path, mtime, filename, size_bytes in log_files:
                if len(selected_files) >= files_per_batch:
                    break
                
                if total_size + size_bytes <= max_size_bytes:
                    selected_files.append(file_path)
                    total_size += size_bytes
                else:
                    # Достигнут лимит размера
                    size_limit_hit = True
                    break
            
            if selected_files:
                total_size_mb = total_size / (1024 * 1024)
                logger.info(f"Sending {len(selected_files)} log files to admin (total: {total_size_mb:.2f} MB)")
                await send_logs_to_admin(selected_files, admin_notifier, logs_dir)
                _last_log_send_time = time.time()
                
                # Удаляем отправленные файлы, чтобы не было повторной отправки
                for file_path in selected_files:
                    try:
                        os.remove(file_path)
                        logger.debug(f"Removed sent log file: {os.path.basename(file_path)}")
                    except Exception as e:
                        logger.warning(f"Failed to remove sent log file {file_path}: {e}")
                
                # Уведомляем админа только если превышен лимит по РАЗМЕРУ
                if size_limit_hit:
                    # Вычисляем размер оставшихся файлов
                    remaining_files = len(log_files) - len(selected_files)
                    remaining_size = sum(f[3] for f in log_files[len(selected_files):])
                    remaining_size_mb = remaining_size / (1024 * 1024)
                    
                    logger.warning(
                        f"Log archive size limit exceeded: {remaining_files} files "
                        f"({remaining_size_mb:.2f} MB) not sent due to {max_total_mb} MB limit"
                    )
                    
                    await admin_notifier.notify(
                        level="WARNING",
                        message=f"Log archive size limit exceeded: {remaining_files} files "
                                f"({remaining_size_mb:.2f} MB) not sent. Consider increasing "
                                f"LOG_ARCHIVE_MAX_TOTAL_MB or reducing LOG_ARCHIVE_INTERVAL_HOURS",
                        event_type="log_archive_limit"
                    )
            else:
                logger.debug("No log files to send")
                
        except Exception as e:
            logger.error(f"Error in periodic log sender: {e}")
