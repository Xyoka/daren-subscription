from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MessageAuthorization, Post, PushRecord, SourceAccount, Subscription, User
from app.services import quota
from app.services.wechat import send_subscribe_message


def preview_text(content: str, length: int = 80) -> str:
    from app.services.crawler import clean_html

    return clean_html(content).replace("\n", " ").strip()[:length]


async def dispatch_post(db: Session, post: Post) -> int:
    if post.is_hidden or post.is_baseline:
        return 0
    account = db.get(SourceAccount, post.source_account_id)
    if not account:
        return 0

    subscriptions = db.scalars(
        select(Subscription)
        .join(User, User.id == Subscription.user_id)
        .where(
            Subscription.source_account_id == account.id,
            Subscription.status == "active",
            User.status == "active",
            User.is_whitelisted.is_(True),
        )
    ).all()

    success_count = 0
    for subscription in subscriptions:
        user = subscription.user
        existing = db.scalar(
            select(PushRecord).where(PushRecord.user_id == user.id, PushRecord.post_id == post.id)
        )
        if existing:
            continue

        now = datetime.now(timezone.utc)
        if not quota.has_daily_push_quota(db, user):
            db.add(
                PushRecord(
                    user_id=user.id,
                    post_id=post.id,
                    push_status="quota_limited",
                    fail_reason="daily push quota exceeded",
                    is_quota_limited=True,
                )
            )
            db.flush()
            continue

        auth = get_available_authorization(db, user.id)
        if not auth:
            if settings.wechat_dry_run:
                # 开发模式下自动创建授权记录，方便端到端测试
                tpl_id = settings.wechat_template_id or "dev_dry_run"
                auth = db.scalar(
                    select(MessageAuthorization).where(
                        MessageAuthorization.user_id == user.id,
                        MessageAuthorization.template_id == tpl_id,
                    )
                )
                if not auth:
                    auth = MessageAuthorization(
                        user_id=user.id,
                        template_id=tpl_id,
                        status="accept",
                        available_count=999,
                    )
                    db.add(auth)
                    db.flush()
            else:
                db.add(
                    PushRecord(
                        user_id=user.id,
                        post_id=post.id,
                        push_status="failed",
                        fail_reason="no available subscribe message authorization",
                    )
                )
                db.flush()
                continue

        ok, error = await send_subscribe_message(
            openid=user.openid,
            post_id=post.id,
            account_name=account.name,
            preview=preview_text(post.content, 50),
            publish_time=post.publish_time.strftime("%Y-%m-%d %H:%M"),
        )
        if ok:
            auth.available_count = max(0, auth.available_count - 1)
            auth.last_used_at = now
            quota.increment_daily_push_count(db, user.id)
            db.add(PushRecord(user_id=user.id, post_id=post.id, push_status="success", push_time=now))
            success_count += 1
        else:
            db.add(PushRecord(user_id=user.id, post_id=post.id, push_status="failed", fail_reason=error))
        db.flush()
    return success_count


def get_available_authorization(db: Session, user_id: int) -> MessageAuthorization | None:
    template_id = settings.wechat_template_id or "default"
    return db.scalar(
        select(MessageAuthorization).where(
            MessageAuthorization.user_id == user_id,
            MessageAuthorization.template_id == template_id,
            MessageAuthorization.status == "accept",
            MessageAuthorization.available_count > 0,
        )
    )

