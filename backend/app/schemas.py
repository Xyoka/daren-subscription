from datetime import datetime

from pydantic import BaseModel, Field


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1)
    dev_openid: str | None = None


class WechatLoginResponse(BaseModel):
    token: str
    openid: str
    is_whitelisted: bool
    user_type: str
    status: str


class AccountOut(BaseModel):
    id: int
    name: str
    profile_url: str
    remark: str | None
    last_crawl_time: datetime | None
    is_subscribed: bool


class SubscribeRequest(BaseModel):
    account_id: int


class SubscribeMessageAuthorizeRequest(BaseModel):
    template_id: str
    status: str
    available_count: int | None = None


class MeOut(BaseModel):
    openid: str
    user_type: str
    status: str
    is_whitelisted: bool
    subscription_count: int
    daily_push_count: int
    daily_push_limit: int
    message_authorized_count: int


class PushRecordOut(BaseModel):
    id: int
    post_id: int
    account_name: str
    publish_time: str
    preview: str
    push_time: str | None


class PostOut(BaseModel):
    id: int
    account_name: str
    publish_time: str
    content: str
    original_url: str

