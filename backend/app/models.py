from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_type: Mapped[str] = mapped_column(String(20), default="free", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")


class SourceAccount(Base):
    __tablename__ = "source_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    xueqiu_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    profile_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    is_baselined: Mapped[bool] = mapped_column(Boolean, default=False)
    last_crawl_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="source_account")
    posts: Mapped[list["Post"]] = relationship(back_populates="source_account")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "source_account_id", name="uq_subscription_user_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_account_id: Mapped[int] = mapped_column(ForeignKey("source_accounts.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="subscriptions")
    source_account: Mapped[SourceAccount] = relationship(back_populates="subscriptions")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("source_account_id", "publish_time", "content_hash", name="uq_post_fallback_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_account_id: Mapped[int] = mapped_column(ForeignKey("source_accounts.id"), index=True)
    xueqiu_post_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    publish_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    original_url: Mapped[str] = mapped_column(String(500))
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_account: Mapped[SourceAccount] = relationship(back_populates="posts")


class PushRecord(Base):
    __tablename__ = "push_records"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_push_user_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    push_status: Mapped[str] = mapped_column(String(30), index=True)
    push_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    fail_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_quota_limited: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_duplicate_blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()
    post: Mapped[Post] = relationship()


class UserDailyUsage(Base):
    __tablename__ = "user_daily_usage"
    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_user_daily_usage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    usage_date: Mapped[date] = mapped_column(Date, index=True)
    success_push_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship()


class MessageAuthorization(Base):
    __tablename__ = "message_authorizations"
    __table_args__ = (UniqueConstraint("user_id", "template_id", name="uq_message_auth_user_template"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    template_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    available_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship()


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_account_id: Mapped[int] = mapped_column(ForeignKey("source_accounts.id"), index=True)
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    found_new_post_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_account: Mapped[SourceAccount] = relationship()

