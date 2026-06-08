"""
基于 Playwright 的雪球浏览器爬虫（纯浏览器方案，不依赖 XUEQIU_COOKIE）。

通过无头浏览器绕过新版阿里云 WAF（renderData 模式），
使用 page.goto() 导航到目标页面获取完整渲染 HTML，
无需手动维护 Cookie。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.crawler import (
    CrawlResult,
    PostCandidate,
    infer_user_id,
    normalize_original_url,
    parse_publish_time,
    parse_timeline_json,
    parse_profile_html,
    _elapsed_ms,
)

logger = logging.getLogger(__name__)

# 浏览器初始化脚本：隐藏自动化痕迹
_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
"""


class XueqiuBrowserCrawler:
    """基于无头浏览器的雪球爬虫，纯浏览器方案，不依赖 Cookie。"""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False

    async def ensure_initialized(self) -> None:
        """确保浏览器已启动且 WAF 挑战已解决。"""
        if self._initialized:
            return
        await self._launch_browser()
        await self._solve_waf()

    async def _launch_browser(self) -> None:
        """启动无头浏览器。"""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-automation",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await self._context.add_init_script(_INIT_SCRIPT)
        self._page = await self._context.new_page()
        logger.info("Browser launched successfully.")

    async def _solve_waf(self) -> None:
        """访问雪球首页以触发并解决 WAF 挑战。"""
        logger.info("Navigating to xueqiu.com to solve WAF...")
        try:
            await self._page.goto(
                "https://xueqiu.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            # 等待 WAF 挑战的 JS 执行完成
            await self._page.wait_for_timeout(3000)
            self._initialized = True
            logger.info("Xueqiu homepage loaded via browser.")
        except Exception as exc:
            logger.error("Failed to load xueqiu.com: %s", exc)
            raise

    async def fetch_latest_posts(
        self,
        xueqiu_user_id: str | None,
        profile_url: str,
        limit: int,
    ) -> CrawlResult:
        """获取博主最新帖子（纯浏览器方案）。

        优先通过浏览器内 API 获取（快速），
        如果 API 失败（需要登录），则降级到打开博主主页获取完整渲染 HTML。
        """
        started = time.perf_counter()
        await self.ensure_initialized()

        user_id = xueqiu_user_id or infer_user_id(profile_url)

        # 第 1 步：浏览器内调用 API（首次尝试，速度快）
        if user_id:
            result = await self._fetch_via_api(user_id, limit, started)
            if result is not None:
                return result

        # 第 2 步：API 失败，浏览器打开博主主页获取完整渲染 HTML
        logger.info("Falling back to profile page navigation for %s", profile_url)
        return await self._fetch_via_page_navigation(profile_url, user_id, limit, started)

    async def _fetch_via_api(
        self, user_id: str, limit: int, started: float
    ) -> CrawlResult | None:
        """通过浏览器内的 fetch 请求 timeline API。"""
        try:
            data = await self._page.evaluate(
                """
                async (args) => {
                    const url = `/v4/statuses/user_timeline.json?user_id=${args.userId}&page=1&count=${args.count}`;
                    const resp = await fetch(url, {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json', 'Referer': 'https://xueqiu.com/' }
                    });
                    const text = await resp.text();
                    if (text.includes('renderData') || text.includes('aliyun_waf')) {
                        return { _waf: true };
                    }
                    try { return JSON.parse(text); } catch(e) { return { _error: text.substring(0, 200) }; }
                }
                """,
                {"userId": user_id, "count": limit},
            )

            if isinstance(data, dict) and data.get("_waf"):
                logger.warning("WAF re-triggered, re-navigating...")
                self._initialized = False
                await self.ensure_initialized()
                return None

            # API 返回错误（如需要登录）
            if isinstance(data, dict) and data.get("error_code"):
                logger.info("Browser API returned error %s, falling back to page navigation.", data.get("error_code"))
                return None

            posts = parse_timeline_json(data, user_id, limit)
            if posts:
                return CrawlResult(
                    posts=posts,
                    response_status=200,
                    duration_ms=_elapsed_ms(started),
                )
        except Exception as exc:
            logger.warning("Browser API fetch failed: %s", exc)

        return None

    async def _fetch_via_page_navigation(
        self, profile_url: str, user_id: str | None, limit: int, started: float
    ) -> CrawlResult:
        """通过浏览器打开博主主页，获取完整渲染 HTML 并解析帖子。

        浏览器导航到博主主页后，页面会加载所有动态内容（包括帖子列表）。
        然后获取 page.content()（完整渲染后的 HTML），从中提取帖子。
        """
        try:
            # 浏览器导航到博主主页，等待页面加载完成
            await self._page.goto(
                profile_url,
                wait_until="networkidle",  # 等待所有网络请求完成
                timeout=30000,
            )
            # 额外等待动态内容渲染
            await self._page.wait_for_timeout(2000)

            # 获取完整渲染后的 HTML
            html = await self._page.content()

            posts = parse_profile_html(html, user_id, limit)
            logger.info(
                "Profile page navigation: found %d posts for %s",
                len(posts), user_id or profile_url,
            )
            return CrawlResult(
                posts=posts,
                response_status=200,
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            logger.error("Browser page navigation failed: %s", exc)
            return CrawlResult(
                posts=[],
                response_status=None,
                duration_ms=_elapsed_ms(started),
            )

    async def close(self) -> None:
        """关闭浏览器释放资源。"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._initialized = False
        logger.info("Browser closed.")

    async def __aenter__(self):
        await self.ensure_initialized()
        return self

    async def __aexit__(self, *args):
        await self.close()
