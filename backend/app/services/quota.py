from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Subscription, User, UserDailyUsage
from app.security import utcnow


def today_local() -> date:
    return utcnow().astimezone(ZoneInfo(settings.timezone)).date()


def subscription_limit(user: User) -> int:
    if user.user_type == "member":
        return settings.member_subscription_limit
    return settings.free_subscription_limit


def daily_push_limit(user: User) -> int:
    if user.user_type == "member":
        return settings.member_daily_push_limit
    return settings.free_daily_push_limit


def active_subscription_count(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ) or 0


def get_daily_usage(db: Session, user_id: int, usage_date: date | None = None) -> UserDailyUsage:
    usage_date = usage_date or today_local()
    usage = db.scalar(
        select(UserDailyUsage).where(
            UserDailyUsage.user_id == user_id,
            UserDailyUsage.usage_date == usage_date,
        )
    )
    if usage:
        return usage
    usage = UserDailyUsage(user_id=user_id, usage_date=usage_date, success_push_count=0)
    db.add(usage)
    db.flush()
    return usage


def current_daily_push_count(db: Session, user_id: int) -> int:
    return get_daily_usage(db, user_id).success_push_count


def has_daily_push_quota(db: Session, user: User) -> bool:
    return current_daily_push_count(db, user.id) < daily_push_limit(user)


def increment_daily_push_count(db: Session, user_id: int) -> None:
    usage = get_daily_usage(db, user_id)
    usage.success_push_count += 1

