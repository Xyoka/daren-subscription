from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings


class WechatError(RuntimeError):
    pass


@dataclass
class AccessTokenCache:
    token: str = ""
    expires_at: datetime = datetime.fromtimestamp(0, timezone.utc)


_token_cache = AccessTokenCache()


async def code_to_openid(code: str, dev_openid: str | None = None) -> str:
    if settings.wechat_dry_run or not settings.wechat_appid or not settings.wechat_secret:
        return dev_openid or f"dev_{code}"

    params = {
        "appid": settings.wechat_appid,
        "secret": settings.wechat_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://api.weixin.qq.com/sns/jscode2session", params=params)
    data = response.json()
    if "openid" not in data:
        raise WechatError(f"jscode2session failed: {data}")
    return data["openid"]


async def get_access_token() -> str:
    now = datetime.now(timezone.utc)
    if _token_cache.token and _token_cache.expires_at > now + timedelta(minutes=5):
        return _token_cache.token
    if not settings.wechat_appid or not settings.wechat_secret:
        raise WechatError("WECHAT_APPID and WECHAT_SECRET are required")

    params = {
        "grant_type": "client_credential",
        "appid": settings.wechat_appid,
        "secret": settings.wechat_secret,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://api.weixin.qq.com/cgi-bin/token", params=params)
    data = response.json()
    if "access_token" not in data:
        raise WechatError(f"access_token failed: {data}")
    _token_cache.token = data["access_token"]
    _token_cache.expires_at = now + timedelta(seconds=int(data.get("expires_in", 7200)))
    return _token_cache.token


def build_subscribe_payload(openid: str, post_id: int, account_name: str, preview: str, publish_time: str) -> dict[str, Any]:
    return {
        "touser": openid,
        "template_id": settings.wechat_template_id,
        "page": f"pages/post/post?id={post_id}",
        "data": {
            "thing1": {"value": account_name[:20]},
            "thing2": {"value": preview[:20]},
            "time3": {"value": publish_time},
        },
    }


async def send_subscribe_message(openid: str, post_id: int, account_name: str, preview: str, publish_time: str) -> tuple[bool, str | None]:
    if settings.wechat_dry_run:
        return True, None
    if not settings.wechat_template_id:
        return False, "WECHAT_TEMPLATE_ID is required"

    token = await get_access_token()
    payload = build_subscribe_payload(openid, post_id, account_name, preview, publish_time)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://api.weixin.qq.com/cgi-bin/message/subscribe/send",
            params={"access_token": token},
            json=payload,
        )
    data = response.json()
    if data.get("errcode") == 0:
        return True, None
    return False, str(data)

