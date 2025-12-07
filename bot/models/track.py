"""
Модель трека для AMusic Bot
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Track(Base):
    """Модель трека"""
    
    __tablename__ = "tracks"
    
    id = Column(Integer, primary_key=True, index=True)
    vk_id = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String(500), nullable=False)
    artist = Column(String(500), nullable=False)
    duration = Column(Integer, nullable=False)  # в секундах
    url = Column(Text, nullable=False)
    quality = Column(Integer, nullable=False)  # битрейт в kbps
    file_size = Column(Integer, nullable=True)  # размер файла в байтах
    cover_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<Track(title='{self.title}', artist='{self.artist}')>"
    
    @property
    def duration_formatted(self) -> str:
        """Форматированная длительность трека"""
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes}:{seconds:02d}"
    
    @property
    def file_size_mb(self) -> float:
        """Размер файла в мегабайтах"""
        if self.file_size:
            return round(self.file_size / (1024 * 1024), 2)
        return 0.0
