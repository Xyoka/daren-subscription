from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user, whitelisted_user
from app.models import MessageAuthorization, Post, PushRecord, SourceAccount, Subscription, User
from app.schemas import (
    AccountOut,
    MeOut,
    PostOut,
    PushRecordOut,
    SubscribeMessageAuthorizeRequest,
    SubscribeRequest,
    WechatLoginRequest,
    WechatLoginResponse,
)
from app.security import create_user_token, utcnow
from app.services import quota
from app.services.push import preview_text
from app.services.wechat import WechatError, code_to_openid
from app.time_utils import format_local_time

router = APIRouter(prefix="/api")


@router.post("/wechat/login", response_model=WechatLoginResponse)
async def wechat_login(payload: WechatLoginRequest, db: Session = Depends(get_db)) -> WechatLoginResponse:
    try:
        openid = await code_to_openid(payload.code, payload.dev_openid)
    except WechatError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    user = db.scalar(select(User).where(User.openid == openid))
    if not user:
        user = User(openid=openid)
        db.add(user)
        db.flush()
    user.last_active_at = utcnow()
    db.commit()
    db.refresh(user)
    return WechatLoginResponse(
        token=create_user_token(user),
        openid=user.openid,
        is_whitelisted=user.is_whitelisted,
        user_type=user.user_type,
        status=user.status,
    )


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> MeOut:
    auth_count = sum(auth.available_count for auth in db.scalars(select(MessageAuthorization).where(MessageAuthorization.user_id == user.id)).all())
    return MeOut(
        openid=user.openid,
        user_type=user.user_type,
        status=user.status,
        is_whitelisted=user.is_whitelisted,
        subscription_count=quota.active_subscription_count(db, user.id),
        daily_push_count=quota.current_daily_push_count(db, user.id),
        daily_push_limit=quota.daily_push_limit(user),
        message_authorized_count=auth_count,
    )


@router.get("/accounts", response_model=list[AccountOut])
def accounts(user: User = Depends(whitelisted_user), db: Session = Depends(get_db)) -> list[AccountOut]:
    active_subs = {
        item.source_account_id
        for item in db.scalars(
            select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active")
        ).all()
    }
    rows = db.scalars(select(SourceAccount).where(SourceAccount.status == "active").order_by(SourceAccount.id)).all()
    return [
        AccountOut(
            id=row.id,
            name=row.name,
            profile_url=row.profile_url,
            remark=row.remark,
            last_crawl_time=row.last_crawl_time,
            is_subscribed=row.id in active_subs,
        )
        for row in rows
    ]


@router.post("/subscriptions")
def subscribe(payload: SubscribeRequest, user: User = Depends(whitelisted_user), db: Session = Depends(get_db)) -> dict[str, str]:
    account = db.get(SourceAccount, payload.account_id)
    if not account or account.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    existing = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.source_account_id == account.id,
        )
    )
    if existing and existing.status == "active":
        return {"status": "already_subscribed"}

    if quota.active_subscription_count(db, user.id) >= quota.subscription_limit(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription limit reached")

    if existing:
        existing.status = "active"
    else:
        db.add(Subscription(user_id=user.id, source_account_id=account.id, status="active"))
    db.commit()
    return {"status": "subscribed"}


@router.delete("/subscriptions/{account_id}")
def unsubscribe(account_id: int, user: User = Depends(whitelisted_user), db: Session = Depends(get_db)) -> dict[str, str]:
    existing = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.source_account_id == account_id,
        )
    )
    if existing:
        existing.status = "canceled"
        db.commit()
    return {"status": "unsubscribed"}


@router.post("/subscribe-message/authorize")
def subscribe_message_authorize(
    payload: SubscribeMessageAuthorizeRequest,
    user: User = Depends(whitelisted_user),
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    auth = db.scalar(
        select(MessageAuthorization).where(
            MessageAuthorization.user_id == user.id,
            MessageAuthorization.template_id == payload.template_id,
        )
    )
    if not auth:
        auth = MessageAuthorization(user_id=user.id, template_id=payload.template_id, available_count=0)
        db.add(auth)

    auth.status = payload.status
    if payload.status == "accept":
        auth.available_count += payload.available_count if payload.available_count is not None else 1
    elif payload.status in {"reject", "ban"}:
        auth.available_count = 0
    db.commit()
    return {"status": auth.status, "available_count": auth.available_count}


@router.get("/push-records", response_model=list[PushRecordOut])
def push_records(user: User = Depends(whitelisted_user), db: Session = Depends(get_db)) -> list[PushRecordOut]:
    records = db.scalars(
        select(PushRecord)
        .join(Post, Post.id == PushRecord.post_id)
        .where(
            PushRecord.user_id == user.id,
            PushRecord.push_status == "success",
            Post.is_hidden.is_(False),
        )
        .order_by(PushRecord.push_time.desc())
        .limit(100)
    ).all()
    return [
        PushRecordOut(
            id=record.id,
            post_id=record.post_id,
            account_name=record.post.source_account.name,
            publish_time=format_local_time(record.post.publish_time),
            preview=preview_text(record.post.content),
            push_time=format_local_time(record.push_time) if record.push_time else None,
        )
        for record in records
    ]


@router.get("/posts/{post_id}", response_model=PostOut)
def post_detail(post_id: int, user: User = Depends(whitelisted_user), db: Session = Depends(get_db)) -> PostOut:
    record = db.scalar(
        select(PushRecord).where(
            PushRecord.user_id == user.id,
            PushRecord.post_id == post_id,
            PushRecord.push_status == "success",
        )
    )
    if not record or record.post.is_hidden:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    post = record.post
    return PostOut(
        id=post.id,
        account_name=post.source_account.name,
        publish_time=format_local_time(post.publish_time),
        content=post.content,
        original_url=post.original_url,
    )

