"""
Сервис статистики использования бота (Redis/in-memory через RedisClient).
"""
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class UsageStatsService:
    """Сервис для сбора и чтения статистики пользователей."""

    USERS_LAST_SEEN_KEY = "stats:users:last_seen"
    USERS_OPS_KEY = "stats:users:operations"
    USER_META_PREFIX = "stats:user:meta:"

    def __init__(self, redis_client):
        self.redis = redis_client

    async def track_activity(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ):
        """Регистрирует активность пользователя."""
        now_ts = time.time()
        member = str(user_id)

        await self.redis.zadd(self.USERS_LAST_SEEN_KEY, {member: now_ts})
        await self.redis.zincrby(self.USERS_OPS_KEY, 1, member)

        meta = {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "",
            "last_name": last_name or "",
            "updated_at": int(now_ts),
        }
        await self.redis.set(
            f"{self.USER_META_PREFIX}{member}",
            json.dumps(meta, ensure_ascii=False),
        )

    async def get_overview(self) -> Dict[str, Any]:
        """Возвращает основные метрики: total/dau/wau/mau."""
        now_ts = time.time()
        day_ago = now_ts - 24 * 60 * 60
        week_ago = now_ts - 7 * 24 * 60 * 60
        month_ago = now_ts - 30 * 24 * 60 * 60

        total_users = await self.redis.zcard(self.USERS_LAST_SEEN_KEY)
        dau = await self.redis.zcount(self.USERS_LAST_SEEN_KEY, day_ago, now_ts)
        wau = await self.redis.zcount(self.USERS_LAST_SEEN_KEY, week_ago, now_ts)
        mau = await self.redis.zcount(self.USERS_LAST_SEEN_KEY, month_ago, now_ts)

        return {
            "total_users": total_users,
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "updated_at": int(now_ts),
        }

    async def get_recent_users(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Последние активные пользователи (по last_seen)."""
        rows = await self.redis.zrevrange(self.USERS_LAST_SEEN_KEY, 0, max(0, limit - 1), withscores=True)
        result: List[Dict[str, Any]] = []

        for member, score in rows:
            user_meta = await self._get_user_meta(member)
            result.append({
                "user_id": int(member),
                "username": user_meta.get("username") or "",
                "first_name": user_meta.get("first_name") or "",
                "last_name": user_meta.get("last_name") or "",
                "last_seen_ts": int(score),
            })

        return result

    async def get_top_users(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Самые активные пользователи (по числу операций)."""
        rows = await self.redis.zrevrange(self.USERS_OPS_KEY, 0, max(0, limit - 1), withscores=True)
        result: List[Dict[str, Any]] = []

        for member, score in rows:
            user_meta = await self._get_user_meta(member)
            result.append({
                "user_id": int(member),
                "username": user_meta.get("username") or "",
                "first_name": user_meta.get("first_name") or "",
                "last_name": user_meta.get("last_name") or "",
                "operations": int(score),
            })

        return result

    async def _get_user_meta(self, user_id_str: str) -> Dict[str, Any]:
        raw = await self.redis.get(f"{self.USER_META_PREFIX}{user_id_str}")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
        return {}

    @staticmethod
    def format_ts(ts: int) -> str:
        """UTC-время для отображения в админ-отчёте."""
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
