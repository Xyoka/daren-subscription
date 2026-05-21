from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import CrawlLog, Post, PushRecord, SourceAccount
from app.security import utcnow
from app.services.crawler import PostCandidate, XueqiuCrawler
from app.services.push import dispatch_post

logger = logging.getLogger(__name__)


async def process_account(
    db: Session,
    account: SourceAccount,
    crawler: XueqiuCrawler | None = None,
    browser_crawler=None,
) -> int:
    # 先用 httpx 爬虫尝试（支持 acw_sc__v2 或 Cookie）
    crawler = crawler or XueqiuCrawler()
    try:
        return await _do_process(db, account, crawler)
    except RuntimeError as exc:
        error_msg = str(exc)
        # httpx 无法使用（WAF、IP封锁等），降级到浏览器爬虫
        if browser_crawler:
            logger.info("httpx crawler failed (%s), falling back to browser for account %s", error_msg[:60], account.name)
            return await _do_process(db, account, browser_crawler)
        else:
                logger.warning("Browser crawler not available for account %s", account.name)
        raise


async def _do_process(db: Session, account: SourceAccount, crawler) -> int:
    try:
        result = await crawler.fetch_latest_posts(account.xueqiu_user_id, account.profile_url, settings.crawl_post_limit)
        # 按发布时间升序处理：推送顺序与发表顺序一致；最新的 candidate 在末尾。
        candidates = sorted(result.posts, key=lambda item: item.publish_time)
        new_count = 0

        if not account.is_baselined:
            # 首轮抓取：把当前 timeline 可见的所有帖都标记为 baseline 入库，
            # 这样后续轮次不会把 baseline 时见到但没入库的帖（含置顶、含较旧的几条）当成新帖。
            latest = None
            for candidate in candidates:
                latest = ensure_post(db, account, candidate, is_baseline=True)
            if latest is not None:
                account.last_post_id = candidates[-1].xueqiu_post_id or latest.content_hash
            account.is_baselined = True
            account.last_crawl_time = utcnow()
            db.add(
                CrawlLog(
                    source_account_id=account.id,
                    crawl_time=utcnow(),
                    status="success",
                    found_new_post_count=0,
                    response_status=result.response_status,
                    duration_ms=result.duration_ms,
                )
            )
            db.commit()
            return 0

        max_age = timedelta(minutes=settings.max_post_age_minutes)
        now = utcnow()
        for candidate in candidates:
            if find_existing_post(db, account.id, candidate):
                continue
            # 年龄窗口兜底：发布时间过旧的，视为换置顶/历史帖，跳过
            if now - candidate.publish_time > max_age:
                logger.info(
                    "Skip stale candidate %s (age=%.0f min) for account %s",
                    candidate.xueqiu_post_id,
                    (now - candidate.publish_time).total_seconds() / 60,
                    account.name,
                )
                continue
            post = ensure_post(db, account, candidate, is_baseline=False)
            db.flush()
            await dispatch_post(db, post)
            new_count += 1

        if candidates:
            latest = candidates[-1]
            account.last_post_id = latest.xueqiu_post_id or latest.content_hash
        account.last_crawl_time = utcnow()
        db.add(
            CrawlLog(
                source_account_id=account.id,
                crawl_time=utcnow(),
                status="success",
                found_new_post_count=new_count,
                response_status=result.response_status,
                duration_ms=result.duration_ms,
            )
        )
        db.commit()
        return new_count
    except Exception as exc:
        db.rollback()
        db.add(
            CrawlLog(
                source_account_id=account.id,
                crawl_time=utcnow(),
                status="failed",
                error_message=str(exc),
            )
        )
        db.commit()
        raise


def ensure_post(db: Session, account: SourceAccount, candidate: PostCandidate, is_baseline: bool) -> Post:
    existing = find_existing_post(db, account.id, candidate)
    if existing:
        return existing
    post = Post(
        source_account_id=account.id,
        xueqiu_post_id=candidate.xueqiu_post_id,
        content=candidate.content,
        content_hash=candidate.content_hash,
        publish_time=candidate.publish_time,
        original_url=candidate.original_url,
        crawl_time=utcnow(),
        is_baseline=is_baseline,
    )
    db.add(post)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = find_existing_post(db, account.id, candidate)
        if existing:
            return existing
        raise
    return post


def find_existing_post(db: Session, account_id: int, candidate: PostCandidate) -> Post | None:
    if candidate.xueqiu_post_id:
        existing = db.scalar(select(Post).where(Post.xueqiu_post_id == candidate.xueqiu_post_id))
        if existing:
            return existing
    return db.scalar(
        select(Post).where(
            Post.source_account_id == account_id,
            Post.publish_time == candidate.publish_time,
            Post.content_hash == candidate.content_hash,
        )
    )


async def run_once(browser_crawler=None) -> int:
    init_db()
    db = SessionLocal()
    try:
        accounts = db.scalars(select(SourceAccount).where(SourceAccount.status == "active")).all()
        total = 0
        for account in accounts:
            try:
                total += await process_account(db, account, browser_crawler=browser_crawler)
            except Exception as exc:
                logger.error("Crawl failed for account %s: %s", account.name, exc)
                db.rollback()
                db.add(
                    CrawlLog(
                        source_account_id=account.id,
                        crawl_time=utcnow(),
                        status="failed",
                        error_message=str(exc),
                    )
                )
                db.commit()
        cleanup_old_records(db)
        db.commit()
        return total
    finally:
        db.close()


def cleanup_old_records(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    db.query(PushRecord).filter(PushRecord.created_at < cutoff).delete(synchronize_session=False)
    db.query(CrawlLog).filter(CrawlLog.created_at < cutoff).delete(synchronize_session=False)


async def main_loop() -> None:
    init_db()
    browser = None
    try:
        from app.services.browser_crawler import XueqiuBrowserCrawler

        browser = XueqiuBrowserCrawler()
        logger.info("Worker started with browser-based WAF fallback.")
    except ImportError as exc:
        logger.warning("Playwright not available, using httpx-only mode: %s", exc)
    except Exception as exc:
        logger.warning("Browser init failed (will retry on demand): %s", exc)

    while True:
        try:
            total = await run_once(browser_crawler=browser)
            if total:
                logger.info("Crawl cycle complete: %d new posts pushed.", total)
        except Exception as exc:
            logger.error("worker run failed: %s", exc, exc_info=True)
        await asyncio.sleep(settings.crawl_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main_loop())

