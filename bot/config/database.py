"""
Настройки базы данных для AMusic Bot
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from bot.config.settings import settings
from bot.models.user import Base as UserBase
from bot.models.track import Base as TrackBase


def get_database_url() -> str:
    """Получить URL базы данных"""
    return settings.database_url


def create_database_engine():
    """Создать движок базы данных"""
    database_url = get_database_url()
    
    # Для SQLite используем синхронный движок
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url,
            echo=False,  # Установить True для отладки SQL запросов
            pool_pre_ping=True
        )
    else:
        # Для PostgreSQL и других используем асинхронный движок
        engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True
        )
    
    return engine


def create_session_factory(engine):
    """Создать фабрику сессий"""
    return sessionmaker(
        bind=engine,
        class_=AsyncSession if hasattr(engine, 'sync') else None,
        expire_on_commit=False
    )


def create_tables(engine):
    """Создать таблицы в базе данных"""
    # Объединяем все базовые классы
    Base = UserBase
    Base.metadata.create_all(bind=engine)


# Создаем глобальные объекты
engine = create_database_engine()
SessionLocal = create_session_factory(engine)


def get_db():
    """Получить сессию базы данных"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
