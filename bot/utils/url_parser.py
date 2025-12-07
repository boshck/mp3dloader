"""
Парсер URL для определения источника и типа контента
"""
import re
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, unquote

logger = logging.getLogger(__name__)


def normalize_vk_url(url: str) -> str:
    """
    Нормализация VK URL
    
    - Приводит к https://vk.com/...
    - Убирает m., new., mobile., vk.me
    - Сохраняет важные параметры: z=, w=, section=
    - Убирает лишние параметры (utm_*, from=, etc)
    
    Args:
        url: Исходный URL
        
    Returns:
        Нормализованный URL
    """
    try:
        # Убираем пробелы
        url = url.strip()
        
        # Добавляем https:// если нет протокола
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # Парсим URL
        parsed = urlparse(url)
        
        # Нормализуем домен
        netloc = parsed.netloc.lower()
        
        # m.vk.com, new.vk.com, mobile.vk.com -> vk.com
        netloc = re.sub(r'^(m\.|new\.|mobile\.)', '', netloc)
        
        # vk.me -> vk.com
        netloc = netloc.replace('vk.me', 'vk.com')
        
        # Парсим query параметры
        query_params = parse_qs(parsed.query)
        
        # Оставляем только важные параметры
        important_params = {}
        for key in ['z', 'w', 'section', 'reply']:
            if key in query_params:
                important_params[key] = query_params[key][0]
        
        # Собираем query string
        query_string = '&'.join([f"{k}={v}" for k, v in important_params.items()])
        
        # Собираем нормализованный URL
        normalized = f"https://{netloc}{parsed.path}"
        if query_string:
            normalized += f"?{query_string}"
        
        logger.debug(f"Normalized VK URL: {url} -> {normalized}")
        return normalized
        
    except Exception as e:
        logger.warning(f"Error normalizing VK URL: {e}, returning original")
        return url


@dataclass
class ParsedURL:
    """Результат разбора URL"""
    source: str  # 'vk', 'youtube', 'soundcloud'
    type: str    # 'playlist', 'track'
    url: str     # нормализованный URL
    ids: Dict[str, Any]  # {'owner_id': ..., 'playlist_id': ..., 'video_id': ..., etc}


def parse_url(url: str) -> Optional[ParsedURL]:
    """
    Разбор URL и определение источника/типа
    
    Args:
        url: URL для разбора
        
    Returns:
        ParsedURL или None если URL не распознан
    """
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()
    
    # Пробуем распознать VK
    vk_result = _parse_vk_url(url)
    if vk_result:
        return vk_result
    
    # Пробуем распознать YouTube
    youtube_result = _parse_youtube_url(url)
    if youtube_result:
        return youtube_result
    
    # Пробуем распознать SoundCloud
    soundcloud_result = _parse_soundcloud_url(url)
    if soundcloud_result:
        return soundcloud_result
    
    return None


def _parse_vk_url(url: str) -> Optional[ParsedURL]:
    """
    Парсинг VK URL
    
    Поддерживаемые форматы:
    - vk.com/audio{owner_id}_{audio_id}
    - vk.com/audio{owner_id}_{audio_id}_{access_key}
    - vk.com/music/playlist/{owner_id}_{playlist_id}
    - vk.com/audios{user_id}?section=...
    - vk.com/wall{owner}_{post}
    - vk.com/?z=audio_playlist{owner}_{playlist}
    - vk.com/?w=wall{owner}_{post}
    """
    try:
        # Нормализуем URL
        normalized_url = normalize_vk_url(url)
        url_lower = normalized_url.lower()
        
        # Парсим URL для доступа к query параметрам
        parsed = urlparse(normalized_url)
        query_params = parse_qs(parsed.query)
        
        # 1. Проверка параметра z= (audio_playlist, audio, wall)
        if 'z' in query_params:
            z_value = unquote(query_params['z'][0])  # Декодируем URL-encoded значения
            
            # z=audio_playlist{owner}_{playlist}[_{hash}]
            z_playlist_pattern = r'audio_playlist(-?\d+)_(\d+)(?:_([a-f0-9]+))?'
            z_playlist_match = re.search(z_playlist_pattern, z_value)
            
            if z_playlist_match:
                owner_id = int(z_playlist_match.group(1))
                playlist_id = int(z_playlist_match.group(2))
                access_hash = z_playlist_match.group(3) if z_playlist_match.group(3) else None
                
                logger.debug(f"Parsed VK playlist from z=: owner_id={owner_id}, playlist_id={playlist_id}")
                
                return ParsedURL(
                    source='vk',
                    type='playlist',
                    url=normalized_url,
                    ids={
                        'owner_id': owner_id,
                        'playlist_id': playlist_id,
                        'access_hash': access_hash
                    }
                )
        
        # 2. Проверка параметра w= (wall posts)
        if 'w' in query_params:
            w_value = unquote(query_params['w'][0])  # Декодируем URL-encoded значения
            
            # w=wall{owner}_{post}
            w_wall_pattern = r'wall(-?\d+)_(\d+)'
            w_wall_match = re.search(w_wall_pattern, w_value)
            
            if w_wall_match:
                owner_id = int(w_wall_match.group(1))
                post_id = int(w_wall_match.group(2))
                
                logger.debug(f"Parsed VK post from w=: owner_id={owner_id}, post_id={post_id}")
                
                return ParsedURL(
                    source='vk',
                    type='post',
                    url=normalized_url,
                    ids={
                        'owner_id': owner_id,
                        'post_id': post_id
                    }
                )
        
        # 3. Паттерн для одиночного трека: audio{owner_id}_{audio_id}
        track_pattern = r'/audio(-?\d+)_(\d+)(?:_([a-f0-9]+))?'
        track_match = re.search(track_pattern, url_lower)
        
        if track_match:
            owner_id = int(track_match.group(1))
            audio_id = int(track_match.group(2))
            access_key = track_match.group(3) if track_match.group(3) else None
            
            logger.debug(f"Parsed VK track: owner_id={owner_id}, audio_id={audio_id}")
            
            return ParsedURL(
                source='vk',
                type='track',
                url=normalized_url,
                ids={
                    'owner_id': owner_id,
                    'audio_id': audio_id,
                    'access_key': access_key
                }
            )
        
        # 4. Паттерн для плейлиста: music/playlist/{owner_id}_{playlist_id}
        playlist_pattern = r'/music/playlist/(-?\d+)_(\d+)(?:_([a-f0-9]+))?'
        playlist_match = re.search(playlist_pattern, url_lower)
        
        if playlist_match:
            owner_id = int(playlist_match.group(1))
            playlist_id = int(playlist_match.group(2))
            access_hash = playlist_match.group(3) if playlist_match.group(3) else None
            
            logger.debug(f"Parsed VK playlist: owner_id={owner_id}, playlist_id={playlist_id}")
            
            return ParsedURL(
                source='vk',
                type='playlist',
                url=normalized_url,
                ids={
                    'owner_id': owner_id,
                    'playlist_id': playlist_id,
                    'access_hash': access_hash
                }
            )
        
        # 5. Паттерн для страницы аудио: audios{user_id}
        audios_pattern = r'/audios(-?\d+)'
        audios_match = re.search(audios_pattern, url_lower)
        
        if audios_match:
            owner_id = int(audios_match.group(1))
            section = query_params.get('section', ['all'])[0]
            
            logger.debug(f"Parsed VK audios page: owner_id={owner_id}, section={section}")
            
            return ParsedURL(
                source='vk',
                type='audios_page',
                url=normalized_url,
                ids={
                    'owner_id': owner_id,
                    'section': section
                }
            )
        
        # 6. Паттерн для поста на стене: wall{owner}_{post}
        wall_pattern = r'/wall(-?\d+)_(\d+)'
        wall_match = re.search(wall_pattern, url_lower)
        
        if wall_match:
            owner_id = int(wall_match.group(1))
            post_id = int(wall_match.group(2))
            
            logger.debug(f"Parsed VK post: owner_id={owner_id}, post_id={post_id}")
            
            return ParsedURL(
                source='vk',
                type='post',
                url=normalized_url,
                ids={
                    'owner_id': owner_id,
                    'post_id': post_id
                }
            )
        
        return None
        
    except Exception as e:
        logger.error(f"Error parsing VK URL: {e}")
        return None


def _parse_youtube_url(url: str) -> Optional[ParsedURL]:
    """
    Парсинг YouTube URL
    
    Поддерживаемые форматы:
    - youtube.com/watch?v=VIDEO_ID
    - youtu.be/VIDEO_ID
    - youtube.com/playlist?list=PLAYLIST_ID
    - youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID (считаем как плейлист)
    """
    try:
        parsed = urlparse(url)
        
        # youtube.com/watch
        if 'youtube.com' in parsed.netloc and '/watch' in parsed.path:
            query_params = parse_qs(parsed.query)
            
            # Проверяем наличие playlist
            if 'list' in query_params:
                playlist_id = query_params['list'][0]
                logger.debug(f"Parsed YouTube playlist: {playlist_id}")
                
                return ParsedURL(
                    source='youtube',
                    type='playlist',
                    url=url,
                    ids={'playlist_id': playlist_id}
                )
            
            # Одиночное видео
            if 'v' in query_params:
                video_id = query_params['v'][0]
                logger.debug(f"Parsed YouTube track: {video_id}")
                
                return ParsedURL(
                    source='youtube',
                    type='track',
                    url=url,
                    ids={'video_id': video_id}
                )
        
        # youtu.be/VIDEO_ID
        if 'youtu.be' in parsed.netloc:
            video_id = parsed.path.lstrip('/')
            if video_id:
                logger.debug(f"Parsed YouTube track (short): {video_id}")
                
                return ParsedURL(
                    source='youtube',
                    type='track',
                    url=url,
                    ids={'video_id': video_id}
                )
        
        # youtube.com/playlist
        if 'youtube.com' in parsed.netloc and '/playlist' in parsed.path:
            query_params = parse_qs(parsed.query)
            if 'list' in query_params:
                playlist_id = query_params['list'][0]
                logger.debug(f"Parsed YouTube playlist: {playlist_id}")
                
                return ParsedURL(
                    source='youtube',
                    type='playlist',
                    url=url,
                    ids={'playlist_id': playlist_id}
                )
        
        return None
        
    except Exception as e:
        logger.error(f"Error parsing YouTube URL: {e}")
        return None


def _parse_soundcloud_url(url: str) -> Optional[ParsedURL]:
    """
    Парсинг SoundCloud URL
    
    Поддерживаемые форматы:
    - soundcloud.com/artist/track
    - soundcloud.com/artist/sets/playlist
    - on.soundcloud.com/...
    
    Примечание: Для SoundCloud нужно использовать yt-dlp для определения типа,
    так как URL не всегда явно указывает на тип контента
    """
    try:
        parsed = urlparse(url)
        
        # soundcloud.com или on.soundcloud.com
        if 'soundcloud.com' in parsed.netloc:
            # Плейлисты обычно содержат /sets/
            if '/sets/' in parsed.path:
                logger.debug(f"Parsed SoundCloud playlist: {url}")
                
                return ParsedURL(
                    source='soundcloud',
                    type='playlist',
                    url=url,
                    ids={'url': url}  # Для SoundCloud используем полный URL
                )
            else:
                # Предполагаем что это трек
                logger.debug(f"Parsed SoundCloud track: {url}")
                
                return ParsedURL(
                    source='soundcloud',
                    type='track',
                    url=url,
                    ids={'url': url}  # Для SoundCloud используем полный URL
                )
        
        return None
        
    except Exception as e:
        logger.error(f"Error parsing SoundCloud URL: {e}")
        return None

