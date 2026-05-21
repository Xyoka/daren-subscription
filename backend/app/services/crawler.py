from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import settings
from app.services import acw_sc_v2

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    posts: list["PostCandidate"]
    response_status: int | None
    duration_ms: int


@dataclass
class PostCandidate:
    xueqiu_post_id: str | None
    content: str
    publish_time: datetime
    original_url: str
    is_pinned: bool = False

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


# 常用 User-Agent 池，用于轮换
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

_WAF_MARKERS = ("aliyun_waf", "_waf_", "renderData", "acw_sc__v2")


class XueqiuCrawler:
    def __init__(self, cookie: str | None = None) -> None:
        self._cookie_str = cookie if cookie is not None else settings.xueqiu_cookie
        self._challenge_solved = False

    async def fetch_latest_posts(self, xueqiu_user_id: str | None, profile_url: str, limit: int) -> CrawlResult:
        started = time.perf_counter()
        user_id = xueqiu_user_id or infer_user_id(profile_url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            # 第 1 步：建立会话（首次访问首页，自动解决 WAF 挑战）
            await self._ensure_session(client)

            # 第 2 步：尝试通过 API 获取帖子
            if user_id:
                url = "https://xueqiu.com/v4/statuses/user_timeline.json"
                response = await client.get(url, params={"user_id": user_id, "page": 1, "count": limit})
                # 如果再次遇到 WAF，重试一次（session 可能过期）
                if _is_waf_response(response.text):
                    logger.warning("WAF challenge reappeared on API request, re-solving...")
                    self._challenge_solved = False
                    await self._ensure_session(client)
                    response = await client.get(url, params={"user_id": user_id, "page": 1, "count": limit})
                if response.status_code == 200 and _looks_like_json(response.text):
                    posts = parse_timeline_json(response.json(), user_id, limit)
                    return CrawlResult(posts=posts, response_status=response.status_code, duration_ms=_elapsed_ms(started))

            # 第 3 步：API 失败时降级解析个人主页 HTML
            response = await client.get(profile_url)
            if _is_waf_response(response.text):
                logger.warning("WAF challenge reappeared on profile page, re-solving...")
                self._challenge_solved = False
                await self._ensure_session(client)
                response = await client.get(profile_url)
            posts = parse_profile_html(response.text, user_id, limit)
            return CrawlResult(posts=posts, response_status=response.status_code, duration_ms=_elapsed_ms(started))

    async def _ensure_session(self, client: httpx.AsyncClient) -> None:
        """确保客户端已建立有效的雪球会话（解决 WAF 挑战）。"""
        if self._challenge_solved:
            return

        # 如果有用户提供的 cookie，直接使用，跳过首页访问（避免 cookie 被覆写）
        if self._cookie_str:
            logger.info("Using configured XUEQIU_COOKIE from .env")
            _set_cookies_from_string(client, self._cookie_str)
            self._challenge_solved = True
            return

        # 访问首页以触发/检查 WAF
        resp = await client.get("https://xueqiu.com/", headers=self._base_headers())

        if resp.status_code == 403:
            # IP 被雪球限制，httpx 无法使用，需要浏览器降级
            raise RuntimeError(
                "XUEQIU_IP_BLOCKED: HTTP 403 from xueqiu.com. "
                "Plesae use browser crawler fallback."
            )

        if _is_waf_response(resp.text):
            logger.info("Detected Aliyun WAF challenge...")
            solved = await self._solve_waf(client, resp.text)
            if not solved:
                # 新版 WAF 无法通过算法求解，但访问首页已获得 session cookie，
                # API 端点（user_timeline.json）不需要 WAF 解决，session cookie 足够
                logger.warning(
                    "WAF challenge could not be solved via algorithm, "
                    "but API calls may still work with session cookies."
                )
        else:
            logger.info("Xueqiu session established successfully.")

        self._challenge_solved = True

    async def _solve_waf(self, client: httpx.AsyncClient, challenge_html: str) -> bool:
        """解决 WAF 挑战并设置 cookie。"""
        try:
            cookie_value = acw_sc_v2.solve(challenge_html)
        except RuntimeError:
            # 新版 WAF 无法通过算法求解，但 API 端点不需要 WAF 解决，
            # session cookie 已足够调用 user_timeline.json
            logger.warning("New WAF format detected, API calls may still work with session cookies.")
            return False

        if not cookie_value:
            logger.error("Could not extract arg1 from WAF challenge page.")
            return False

        # 设置求解出的 acw_sc__v2 cookie（仅旧版 WAF 需要）
        client.cookies.set("acw_sc__v2", cookie_value)
        logger.debug("Set acw_sc__v2 cookie: %s", cookie_value[:10] + "...")

        # 携带已解决的 cookie 重新访问首页，获取完整的会话 cookie
        await asyncio.sleep(0.5)
        resp = await client.get("https://xueqiu.com/", headers=self._base_headers())

        if _is_waf_response(resp.text):
            logger.error("WAF challenge still present after setting acw_sc__v2 cookie.")
            return False

        logger.info("WAF challenge solved, session established successfully.")
        return True

    def _base_headers(self) -> dict[str, str]:
        """返回基础请求头（不含 Cookie，Cookie 由 client.cookies 管理）。"""
        return {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://xueqiu.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }


def _set_cookies_from_string(client: httpx.AsyncClient, cookie_str: str) -> None:
    """将 cookie 字符串解析并设置到客户端。"""
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            client.cookies.set(key.strip(), value.strip())


def _is_waf_response(text: str) -> bool:
    """检测响应是否为阿里云 WAF 挑战页面。"""
    return any(marker in text for marker in _WAF_MARKERS)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def infer_user_id(profile_url: str) -> str | None:
    match = re.search(r"/u/(\d+)", profile_url)
    if match:
        return match.group(1)
    match = re.search(r"user_id=(\d+)", profile_url)
    if match:
        return match.group(1)
    return None


def _extract_item_text(item: dict) -> str:
    """从 API 返回的帖子 item 中提取文本，优先获取完整内容。"""
    return str(
        item.get("longTextForIOS")
        or item.get("text")
        or item.get("description")
        or item.get("title")
        or ""
    )


def _extract_item_images(item: dict) -> list[str]:
    """从 API 返回的帖子 item 中提取图片 URL 列表。"""
    pic = item.get("pic") or ""
    if not pic:
        return []
    return [url.strip() for url in pic.split(",") if url.strip()]


def _build_image_html(urls: list[str]) -> str:
    """将图片 URL 列表转换为 HTML <img> 标签字符串。"""
    if not urls:
        return ""
    parts = []
    for url in urls:
        # 将缩略图 URL 转为原图（移除 !thumb.jpg 后缀）
        original_url = re.sub(r"!thumb\.jpg$", "", url)
        parts.append(f'<img src="{original_url}" />')
    return "<br>" + "<br>".join(parts) if parts else ""


def _is_pinned_item(item: dict) -> bool:
    """识别 timeline 中的置顶帖（不同雪球接口的字段命名不一致，做宽松匹配）。"""
    for key in ("pinned", "is_top", "top", "is_pinned", "is_sticky", "sticky"):
        value = item.get(key)
        if value in (1, True, "1", "true", "True"):
            return True
    # 雪球部分接口用 `mark` 整型表示置顶/精华等标记
    mark = item.get("mark")
    if isinstance(mark, int) and mark > 0:
        return True
    return False


def parse_timeline_json(data: dict[str, Any], user_id: str, limit: int) -> list[PostCandidate]:
    raw_items = data.get("statuses") or data.get("list") or data.get("data", {}).get("statuses") or []
    posts: list[PostCandidate] = []
    for item in raw_items:
        if _is_pinned_item(item):
            post_id = str(item.get("id") or item.get("idstr") or "") or "<unknown>"
            logger.info("Skip pinned timeline item %s", post_id)
            continue

        post_id = str(item.get("id") or item.get("idstr") or item.get("status_id") or "") or None

        # 获取文本内容（优先获取完整内容 longTextForIOS）
        content = _preserve_content(_extract_item_text(item))

        # 补全帖子中的图片
        content += _build_image_html(_extract_item_images(item))

        # 如果是转发/回复帖，追加原帖内容以提供上下文
        retweeted = item.get("retweeted_status")
        if retweeted:
            original_text = _preserve_content(_extract_item_text(retweeted))
            original_text += _build_image_html(_extract_item_images(retweeted))
            if original_text:
                content = f"{content}<br><br>--- 原帖内容 ---<br>{original_text}"

        if not content:
            continue
        publish_time = parse_publish_time(item.get("created_at") or item.get("timeBefore") or item.get("createdAt"))
        target = item.get("target") or item.get("url") or ""
        original_url = normalize_original_url(target, user_id, post_id)
        posts.append(PostCandidate(post_id, content, publish_time, original_url))

    # 不依赖 API 返回顺序：统一按发布时间降序后截断
    posts.sort(key=lambda p: p.publish_time, reverse=True)
    return posts[:limit]


def parse_profile_html(text: str, user_id: str | None, limit: int) -> list[PostCandidate]:
    posts: list[PostCandidate] = []
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", text, flags=re.S | re.I):
        script = html.unescape(match.group(1))
        if "statuses" not in script and "timeline" not in script:
            continue
        for obj_text in re.findall(r"\{[^{}]*(?:\"text\"|\"description\")[^{}]*\}", script):
            try:
                item = json.loads(obj_text)
            except json.JSONDecodeError:
                continue
            posts.extend(parse_timeline_json({"statuses": [item]}, user_id or "", 1))
            if len(posts) >= limit:
                return posts[:limit]
    return posts[:limit]


def _preserve_content(value: str) -> str:
    """
    保留原始 HTML 格式的内容，仅做基本清理。
    - 保留所有 HTML 标签
    - HTML 实体解码（如 &amp; → &）
    - 清理多余空白
    """
    value = html.unescape(value)
    value = re.sub(r"\s+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_html(value: str) -> str:
    """
    将 HTML 内容转换为纯文本（用于推送预览等显示场景）。
    """
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def parse_publish_time(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)
    if isinstance(value, str) and value:
        for parser in (_parse_iso, _parse_email_date):
            parsed = parser(value)
            if parsed:
                return parsed
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_email_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_original_url(target: str, user_id: str, post_id: str | None) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if target.startswith("/"):
        return f"https://xueqiu.com{target}"
    if user_id and post_id:
        return f"https://xueqiu.com/{user_id}/{post_id}"
    return "https://xueqiu.com/"
