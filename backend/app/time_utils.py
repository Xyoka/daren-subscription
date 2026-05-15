from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.config import settings


def format_local_time(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """将 UTC datetime 转换为配置的时区时间（默认北京时间）并格式化。"""
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_tz = timezone(timedelta(hours=8))
    local_dt = dt.astimezone(local_tz)
    return local_dt.strftime(fmt)
