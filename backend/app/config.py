from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "达人订阅"
    environment: str = "development"
    database_url: str = "sqlite:///./darensub.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret"
    admin_username: str = "admin"
    admin_password: str = "admin"

    wechat_appid: str = ""
    wechat_secret: str = ""
    wechat_template_id: str = ""
    wechat_dry_run: bool = True
    api_base_url: str = "http://127.0.0.1:8000"

    xueqiu_cookie: str = ""
    crawl_interval_seconds: int = 120
    crawl_post_limit: int = 5
    retention_days: int = 30
    timezone: str = "Asia/Shanghai"

    free_subscription_limit: int = 3
    free_daily_push_limit: int = 5
    member_subscription_limit: int = 30
    member_daily_push_limit: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

