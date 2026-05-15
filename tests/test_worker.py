from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import MessageAuthorization, Post, PushRecord, SourceAccount, Subscription, User, UserDailyUsage
from app.services.crawler import CrawlResult, PostCandidate
from app.services.worker import process_account


class FakeCrawler:
    def __init__(self, posts):
        self.posts = posts

    async def fetch_latest_posts(self, xueqiu_user_id, profile_url, limit):
        return CrawlResult(posts=self.posts, response_status=200, duration_ms=1)


def candidate(post_id, minutes, *, publish_time=None):
    return PostCandidate(
        xueqiu_post_id=post_id,
        content=f"content {post_id}",
        publish_time=publish_time or datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        original_url=f"https://xueqiu.com/1/{post_id}",
    )


@pytest.mark.asyncio
async def test_first_crawl_only_builds_baseline(db_session):
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1")
    db_session.add(account)
    db_session.commit()

    count = await process_account(db_session, account, FakeCrawler([candidate("p1", 0)]))
    assert count == 0
    assert account.is_baselined is True
    assert db_session.scalars(select(PushRecord)).all() == []
    # 候选 1 条 → posts 表 1 条
    assert db_session.scalar(select(Post).where(Post.source_account_id == account.id)).xueqiu_post_id == "p1"


def fresh_candidate(post_id, seconds_ago):
    """生成 publish_time 在 now-seconds_ago 的候选帖（用于绕过年龄窗口）。"""
    return PostCandidate(
        xueqiu_post_id=post_id,
        content=f"content {post_id}",
        publish_time=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        original_url=f"https://xueqiu.com/1/{post_id}",
    )


@pytest.mark.asyncio
async def test_quota_limited_push_is_logged(db_session):
    user = User(openid="u1", is_whitelisted=True)
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1", is_baselined=True)
    db_session.add_all([user, account])
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, source_account_id=account.id, status="active"))
    db_session.add(MessageAuthorization(user_id=user.id, template_id="default", status="accept", available_count=10))
    db_session.commit()

    # 全部在年龄窗口内（10..60 秒前），不会被 stale 过滤
    posts = [fresh_candidate(f"p{index}", 10 + index * 10) for index in range(6)]
    count = await process_account(db_session, account, FakeCrawler(posts))
    assert count == 6

    records = db_session.scalars(select(PushRecord).order_by(PushRecord.id)).all()
    assert [record.push_status for record in records].count("success") == 5
    assert [record.push_status for record in records].count("quota_limited") == 1
    usage = db_session.scalar(select(UserDailyUsage).where(UserDailyUsage.user_id == user.id))
    assert usage.success_push_count == 5


@pytest.mark.asyncio
async def test_baseline_records_all_visible_posts(db_session):
    """首轮抓取：返回 5 条 → 5 条全部以 is_baseline=True 入库，0 PushRecord。"""
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1")
    db_session.add(account)
    db_session.commit()

    posts = [fresh_candidate(f"p{i}", i * 30) for i in range(5)]
    count = await process_account(db_session, account, FakeCrawler(posts))

    assert count == 0
    assert account.is_baselined is True
    saved = db_session.scalars(select(Post).where(Post.source_account_id == account.id)).all()
    assert len(saved) == 5
    assert all(p.is_baseline for p in saved)
    assert db_session.scalars(select(PushRecord)).all() == []


@pytest.mark.asyncio
async def test_pinned_like_old_post_not_pushed_after_baseline(db_session):
    """同一批 candidates（含 1 条 4 个月前的"置顶")两轮抓取 → 第 2 轮 0 新帖、0 PushRecord。"""
    user = User(openid="u1", is_whitelisted=True)
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1")
    db_session.add_all([user, account])
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, source_account_id=account.id, status="active"))
    db_session.add(MessageAuthorization(user_id=user.id, template_id="default", status="accept", available_count=10))
    db_session.commit()

    pinned = candidate(
        "pinned-old",
        0,
        publish_time=datetime.now(timezone.utc) - timedelta(days=120),
    )
    fresh_posts = [fresh_candidate(f"p{i}", 10 + i * 10) for i in range(4)]
    batch = fresh_posts + [pinned]

    # 轮 1：baseline 全部入库
    count1 = await process_account(db_session, account, FakeCrawler(batch))
    assert count1 == 0
    assert db_session.scalar(select(Post).where(Post.xueqiu_post_id == "pinned-old")) is not None

    # 轮 2：同一批返回 → 全部命中 find_existing_post → 0 新帖
    count2 = await process_account(db_session, account, FakeCrawler(batch))
    assert count2 == 0
    assert db_session.scalars(select(PushRecord)).all() == []


@pytest.mark.asyncio
async def test_stale_post_filtered_by_age_window(db_session, monkeypatch):
    """baseline 后突然冒出一条 4 小时前的帖子（max_post_age_minutes=60）→ 跳过、0 PushRecord。"""
    from app.services import worker as worker_mod

    monkeypatch.setattr(worker_mod.settings, "max_post_age_minutes", 60)

    user = User(openid="u1", is_whitelisted=True)
    account = SourceAccount(
        name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1", is_baselined=True
    )
    db_session.add_all([user, account])
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, source_account_id=account.id, status="active"))
    db_session.add(MessageAuthorization(user_id=user.id, template_id="default", status="accept", available_count=10))
    db_session.commit()

    stale = candidate(
        "stale-1",
        0,
        publish_time=datetime.now(timezone.utc) - timedelta(hours=4),
    )
    count = await process_account(db_session, account, FakeCrawler([stale]))

    assert count == 0
    assert db_session.scalar(select(Post).where(Post.xueqiu_post_id == "stale-1")) is None
    assert db_session.scalars(select(PushRecord)).all() == []

