from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import CrawlLog, MessageAuthorization, Post, PushRecord, SourceAccount, Subscription, User
from app.security import create_admin_token, verify_admin_request
from app.services.quota import get_daily_usage
from app.services.wechat import send_subscribe_message
from app.services.worker import process_account

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def redirect(path: str = "/admin") -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/login", response_model=None)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username != settings.admin_username or password != settings.admin_password:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "账号或密码错误"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = redirect("/admin")
    response.set_cookie("admin_session", create_admin_token(), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = redirect("/admin/login")
    response.delete_cookie("admin_session")
    return response


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    verify_admin_request(request)
    users = db.scalars(select(User).order_by(User.id.desc()).limit(20)).all()
    usage_by_user = {user.id: get_daily_usage(db, user.id).success_push_count for user in users}
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "users": users,
            "usage_by_user": usage_by_user,
            "accounts": db.scalars(select(SourceAccount).order_by(SourceAccount.id.desc()).limit(20)).all(),
            "posts": db.scalars(select(Post).order_by(Post.id.desc()).limit(20)).all(),
            "push_logs": db.scalars(select(PushRecord).order_by(PushRecord.id.desc()).limit(20)).all(),
            "crawl_logs": db.scalars(select(CrawlLog).order_by(CrawlLog.id.desc()).limit(20)).all(),
        },
    )


@router.post("/users/{user_id}")
def update_user(
    request: Request,
    user_id: int,
    user_type: str = Form(...),
    status_value: str = Form(...),
    is_whitelisted: str | None = Form(default=None),
    reset_usage: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_admin_request(request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user.user_type = user_type
    user.status = status_value
    user.is_whitelisted = is_whitelisted == "on"
    if reset_usage == "on":
        get_daily_usage(db, user.id).success_push_count = 0
    if user_type == "free":
        active_subs = db.scalars(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.status == "active")
            .order_by(Subscription.created_at)
        ).all()
        for sub in active_subs[settings.free_subscription_limit :]:
            sub.status = "admin_disabled"
    db.commit()
    return redirect()


@router.post("/accounts")
def create_account(
    request: Request,
    name: str = Form(...),
    profile_url: str = Form(...),
    xueqiu_user_id: str | None = Form(default=None),
    remark: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_admin_request(request)
    db.add(SourceAccount(name=name, profile_url=profile_url, xueqiu_user_id=xueqiu_user_id or None, remark=remark))
    db.commit()
    return redirect()


@router.post("/accounts/{account_id}")
def update_account(
    request: Request,
    account_id: int,
    name: str = Form(...),
    profile_url: str = Form(...),
    xueqiu_user_id: str | None = Form(default=None),
    status_value: str = Form(...),
    remark: str | None = Form(default=None),
    reset_baseline: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_admin_request(request)
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(status_code=404)
    account.name = name
    account.profile_url = profile_url
    account.xueqiu_user_id = xueqiu_user_id or None
    account.status = status_value
    account.remark = remark
    if reset_baseline == "on":
        account.is_baselined = False
        account.last_post_id = None
    db.commit()
    return redirect()


@router.post("/accounts/{account_id}/crawl-test")
async def crawl_test(request: Request, account_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(status_code=404)
    await process_account(db, account)
    return redirect()


@router.post("/posts/{post_id}/toggle-hidden")
def toggle_post_hidden(request: Request, post_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404)
    post.is_hidden = not post.is_hidden
    db.commit()
    return redirect()


@router.post("/test-push")
async def test_push(request: Request, user_id: int = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    auth = db.scalar(
        select(MessageAuthorization).where(
            MessageAuthorization.user_id == user.id,
            MessageAuthorization.status == "accept",
            MessageAuthorization.available_count > 0,
        )
    )
    if not auth:
        db.add(PushRecord(user_id=user.id, post_id=create_admin_test_post(db).id, push_status="failed", fail_reason="no available subscribe message authorization for admin test"))
        db.commit()
        return redirect()
    post = create_admin_test_post(db)
    ok, error = await send_subscribe_message(
        user.openid,
        post.id,
        "达人订阅",
        "这是一条后台测试提醒",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    db.add(
        PushRecord(
            user_id=user.id,
            post_id=post.id,
            push_status="success" if ok else "failed",
            push_time=datetime.now(timezone.utc) if ok else None,
            fail_reason=error,
        )
    )
    if ok:
        auth.available_count = max(0, auth.available_count - 1)
        auth.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return redirect()


def create_admin_test_post(db: Session) -> Post:
    account = db.scalar(select(SourceAccount).where(SourceAccount.name == "后台测试"))
    if not account:
        account = SourceAccount(name="后台测试", profile_url="https://xueqiu.com/", status="inactive", is_baselined=True)
        db.add(account)
        db.flush()
    now = datetime.now(timezone.utc)
    post = Post(
        source_account_id=account.id,
        xueqiu_post_id=f"admin-test-{int(now.timestamp() * 1000)}",
        content="这是一条后台测试提醒。",
        content_hash=f"admin-test-{int(now.timestamp() * 1000)}",
        publish_time=now,
        original_url="https://xueqiu.com/",
        crawl_time=now,
        is_baseline=True,
    )
    db.add(post)
    db.flush()
    return post
