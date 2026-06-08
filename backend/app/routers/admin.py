from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import CrawlLog, MessageAuthorization, Post, PushRecord, SourceAccount, Subscription, User
from app.security import create_admin_token, verify_admin_request
from app.services.crawler import clean_html
from app.services.quota import get_daily_usage
from app.services.wechat import send_subscribe_message
from app.services.worker import process_account
from app.time_utils import format_local_time

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
templates.env.filters["clean_html"] = lambda v: clean_html(v) if v else ""
templates.env.filters["local_time"] = format_local_time


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
def dashboard(
    request: Request,
    error: str = "",
    success: str = "",
    users_page: int = 1,
    accounts_page: int = 1,
    posts_page: int = 1,
    push_page: int = 1,
    crawl_page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    verify_admin_request(request)
    per_page = 20

    def _paginate(query, page_num):
        page_num = max(1, page_num)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = db.scalars(query.offset((page_num - 1) * per_page).limit(per_page)).all()
        return items, total, page_num

    # 用户（全部展示）
    all_users = db.scalars(select(User).order_by(User.id.desc())).all()
    users = all_users[:30]

    # 各栏目分页
    accounts, total_accounts, accounts_page = _paginate(
        select(SourceAccount).order_by(SourceAccount.id.desc()), accounts_page
    )
    posts, total_posts, posts_page = _paginate(
        select(Post).order_by(Post.id.desc()), posts_page
    )
    push_logs, total_push, push_page = _paginate(
        select(PushRecord).order_by(PushRecord.id.desc()), push_page
    )
    crawl_logs, total_crawl, crawl_page = _paginate(
        select(CrawlLog).order_by(CrawlLog.id.desc()), crawl_page
    )

    usage_by_user = {user.id: get_daily_usage(db, user.id).success_push_count for user in users}
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "users": users,
            "usage_by_user": usage_by_user,
            "accounts": accounts,
            "posts": posts,
            "push_logs": push_logs,
            "crawl_logs": crawl_logs,
            "error": error,
            "success": success,
            "per_page": per_page,
            # 每个栏目的分页信息
            "pagers": {
                "accounts": {"page": accounts_page, "total": total_accounts, "param": "accounts_page"},
                "posts": {"page": posts_page, "total": total_posts, "param": "posts_page"},
                "push": {"page": push_page, "total": total_push, "param": "push_page"},
                "crawl": {"page": crawl_page, "total": total_crawl, "param": "crawl_page"},
            },
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
async def crawl_test(request: Request, account_id: int, db: Session = Depends(get_db)):
    verify_admin_request(request)
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(status_code=404)
    try:
        await process_account(db, account)
        return redirect("/admin?success=抓取测试完成")
    except RuntimeError as exc:
        # httpx 失败，尝试浏览器降级
        try:
            from app.services.browser_crawler import XueqiuBrowserCrawler

            browser = XueqiuBrowserCrawler()
            await browser.ensure_initialized()
            await process_account(db, account, browser_crawler=browser)
            await browser.close()
            return redirect("/admin?success=抓取测试完成（浏览器模式）")
        except Exception as browser_exc:
            error_msg = str(exc)
            return redirect(f"/admin?error=抓取失败：{error_msg[:200]}")


@router.post("/posts/{post_id}/toggle-hidden")
def toggle_post_hidden(request: Request, post_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404)
    post.is_hidden = not post.is_hidden
    db.commit()
    return redirect()


@router.post("/posts/{post_id}/delete")
def delete_post(request: Request, post_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404)
    db.execute(delete(PushRecord).where(PushRecord.post_id == post_id))
    db.delete(post)
    db.commit()
    return redirect("/admin?success=帖子已删除")


@router.post("/accounts/{account_id}/clear-posts")
def clear_account_posts(request: Request, account_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """清空该博主的全部帖子/抓取日志/推送记录，并重置基线状态。
    保留博主本体和订阅关系，下一轮抓取会重建基线。"""
    verify_admin_request(request)
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(status_code=404)

    post_ids = [pid for (pid,) in db.execute(select(Post.id).where(Post.source_account_id == account_id)).all()]
    if post_ids:
        db.execute(delete(PushRecord).where(PushRecord.post_id.in_(post_ids)))
        db.execute(delete(Post).where(Post.id.in_(post_ids)))
    db.execute(delete(CrawlLog).where(CrawlLog.source_account_id == account_id))
    account.is_baselined = False
    account.last_post_id = None
    account.last_crawl_time = None
    db.commit()
    return redirect(f"/admin?success=已清空 {account.name} 的帖子和日志，下一轮抓取将重建基线")


@router.post("/accounts/{account_id}/delete")
def delete_account(request: Request, account_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """删除博主：级联清理帖子、抓取日志、推送记录、订阅关系，最后删除博主本体。"""
    verify_admin_request(request)
    account = db.get(SourceAccount, account_id)
    if not account:
        raise HTTPException(status_code=404)
    name = account.name

    post_ids = [pid for (pid,) in db.execute(select(Post.id).where(Post.source_account_id == account_id)).all()]
    if post_ids:
        db.execute(delete(PushRecord).where(PushRecord.post_id.in_(post_ids)))
        db.execute(delete(Post).where(Post.id.in_(post_ids)))
    db.execute(delete(CrawlLog).where(CrawlLog.source_account_id == account_id))
    db.execute(delete(Subscription).where(Subscription.source_account_id == account_id))
    db.delete(account)
    db.commit()
    return redirect(f"/admin?success=博主 {name} 已删除")


@router.post("/crawl-logs/clear")
def clear_crawl_logs(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    count = db.execute(delete(CrawlLog)).rowcount or 0
    db.commit()
    return redirect(f"/admin?success=已清空 {count} 条抓取日志")


@router.post("/push-logs/clear")
def clear_push_logs(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    verify_admin_request(request)
    count = db.execute(delete(PushRecord)).rowcount or 0
    db.commit()
    return redirect(f"/admin?success=已清空 {count} 条推送日志")


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
