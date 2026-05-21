"""
基于 Playwright 的雪球浏览器爬虫。

通过无头浏览器绕过新版阿里云 WAF（renderData 模式），
解决纯 HTTP 请求无法解决的 JS 挑战问题。

使用方式：
    crawler = XueqiuBrowserCrawler()
    result = await crawler.fetch_latest_posts(user_id, profile_url, limit)
    await crawler.close()
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.services.crawler import (
    CrawlResult,
    PostCandidate,
    infer_user_id,
    normalize_original_url,
    parse_publish_time,
    parse_timeline_json,
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
    """基于无头浏览器的雪球爬虫，可绕过新版阿里云 WAF。"""

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

    async def _set_cookies_from_str(self, cookie_str: str) -> None:
        """将 cookie 字符串解析并设置到浏览器上下文。"""
        if not self._context:
            return
        cookies = []
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookies.append({
                    "name": key.strip(),
                    "value": value.strip(),
                    "domain": ".xueqiu.com",
                    "path": "/",
                })
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info("Set %d cookies on browser context.", len(cookies))

    async def _solve_waf(self) -> None:
        """加载雪球首页以触发并解决 WAF 挑战。"""
        logger.info("Loading xueqiu.com to solve WAF challenge...")
        try:
            # 先设置 XUEQIU_COOKIE 再访问首页，避免 IP 被限制
            if settings.xueqiu_cookie:
                await self._set_cookies_from_str(settings.xueqiu_cookie)
            await self._page.goto(
                "https://xueqiu.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            # 等待 WAF 挑战的 JS 执行完成
            await self._page.wait_for_timeout(3000)
            self._initialized = True
            logger.info("WAF challenge solved via browser.")
        except Exception as exc:
            logger.error("Failed to load xueqiu.com: %s", exc)
            raise

    async def fetch_latest_posts(
        self,
        xueqiu_user_id: str | None,
        profile_url: str,
        limit: int,
    ) -> CrawlResult:
        """获取博主最新帖子。

        先在浏览器上下文中通过 API 获取，如果失败则降级解析主页 HTML。
        """
        started = time.perf_counter()
        await self.ensure_initialized()

        user_id = xueqiu_user_id or infer_user_id(profile_url)

        # 优先通过 API 获取
        if user_id:
            result = await self._fetch_via_api(user_id, limit, started)
            if result is not None:
                return result

        # API 失败，降级解析主页 HTML
        return await self._fetch_via_profile(profile_url, user_id, started)

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
                    try { return JSON.parse(text); } catch(e) { return { _parseError: text.substring(0, 200) }; }
                }
                """,
                {"userId": user_id, "count": limit},
            )

            if isinstance(data, dict) and data.get("_waf"):
                logger.warning("WAF re-triggered in browser, need to re-solve.")
                self._initialized = False
                await self.ensure_initialized()
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

    async def _fetch_via_profile(
        self, profile_url: str, user_id: str | None, started: float
    ) -> CrawlResult:
        """通过浏览器加载博主主页 HTML 并解析帖子。"""
        try:
            html = await self._page.evaluate(
                """
                async (url) => {
                    const resp = await fetch(url, {
                        credentials: 'include',
                        headers: { 'Accept': 'text/html', 'Referer': 'https://xueqiu.com/' }
                    });
                    return await resp.text();
                }
                """,
                profile_url,
            )

            from app.services.crawler import parse_profile_html

            posts = parse_profile_html(html, user_id, settings.crawl_post_limit)
            return CrawlResult(
                posts=posts,
                response_status=200,
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            logger.error("Browser profile fetch failed: %s", exc)
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
