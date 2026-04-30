import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import settings


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

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class XueqiuCrawler:
    def __init__(self, cookie: str | None = None) -> None:
        self.cookie = cookie if cookie is not None else settings.xueqiu_cookie

    async def fetch_latest_posts(self, xueqiu_user_id: str | None, profile_url: str, limit: int) -> CrawlResult:
        started = time.perf_counter()
        headers = self._headers()
        user_id = xueqiu_user_id or infer_user_id(profile_url)
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
            if user_id:
                url = "https://xueqiu.com/statuses/original/timeline.json"
                response = await client.get(url, params={"user_id": user_id, "page": 1, "count": limit})
                ensure_not_blocked(response.text)
                if response.status_code == 200 and _looks_like_json(response.text):
                    posts = parse_timeline_json(response.json(), user_id, limit)
                    return CrawlResult(posts=posts, response_status=response.status_code, duration_ms=_elapsed_ms(started))

            response = await client.get(profile_url)
            ensure_not_blocked(response.text)
            posts = parse_profile_html(response.text, user_id, limit)
            return CrawlResult(posts=posts, response_status=response.status_code, duration_ms=_elapsed_ms(started))

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
            "Accept": "application/json,text/html,application/xhtml+xml",
            "Referer": "https://xueqiu.com/",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def ensure_not_blocked(text: str) -> None:
    markers = ("aliyun_waf", "_waf_", "renderData")
    if any(marker in text for marker in markers):
        raise RuntimeError("Xueqiu returned an Aliyun WAF challenge instead of post content. Configure a valid XUEQIU_COOKIE or verify server-side access.")


def infer_user_id(profile_url: str) -> str | None:
    match = re.search(r"/u/(\d+)", profile_url)
    if match:
        return match.group(1)
    match = re.search(r"user_id=(\d+)", profile_url)
    if match:
        return match.group(1)
    return None


def parse_timeline_json(data: dict[str, Any], user_id: str, limit: int) -> list[PostCandidate]:
    raw_items = data.get("statuses") or data.get("list") or data.get("data", {}).get("statuses") or []
    posts: list[PostCandidate] = []
    for item in raw_items[:limit]:
        post_id = str(item.get("id") or item.get("idstr") or item.get("status_id") or "") or None
        content = clean_html(str(item.get("text") or item.get("description") or item.get("title") or ""))
        if not content:
            continue
        publish_time = parse_publish_time(item.get("created_at") or item.get("timeBefore") or item.get("createdAt"))
        target = item.get("target") or item.get("url") or ""
        original_url = normalize_original_url(target, user_id, post_id)
        posts.append(PostCandidate(post_id, content, publish_time, original_url))
    return posts


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


def clean_html(value: str) -> str:
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
