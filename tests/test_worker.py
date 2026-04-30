from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import MessageAuthorization, PushRecord, SourceAccount, Subscription, User, UserDailyUsage
from app.services.crawler import CrawlResult, PostCandidate
from app.services.worker import process_account


class FakeCrawler:
    def __init__(self, posts):
        self.posts = posts

    async def fetch_latest_posts(self, xueqiu_user_id, profile_url, limit):
        return CrawlResult(posts=self.posts, response_status=200, duration_ms=1)


def candidate(post_id, minutes):
    return PostCandidate(
        xueqiu_post_id=post_id,
        content=f"content {post_id}",
        publish_time=datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
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


@pytest.mark.asyncio
async def test_quota_limited_push_is_logged(db_session):
    user = User(openid="u1", is_whitelisted=True)
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1", is_baselined=True)
    db_session.add_all([user, account])
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, source_account_id=account.id, status="active"))
    db_session.add(MessageAuthorization(user_id=user.id, template_id="default", status="accept", available_count=10))
    db_session.commit()

    posts = [candidate(f"p{index}", index) for index in range(6)]
    count = await process_account(db_session, account, FakeCrawler(posts))
    assert count == 6

    records = db_session.scalars(select(PushRecord).order_by(PushRecord.id)).all()
    assert [record.push_status for record in records].count("success") == 5
    assert [record.push_status for record in records].count("quota_limited") == 1
    usage = db_session.scalar(select(UserDailyUsage).where(UserDailyUsage.user_id == user.id))
    assert usage.success_push_count == 5

