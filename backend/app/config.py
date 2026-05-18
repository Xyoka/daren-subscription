import os
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
    # 兜底：发布时间早于 now - max_post_age_minutes 的帖子不视为新帖，避免置顶/换置顶被误推
    max_post_age_minutes: int = 180
    retention_days: int = 30
    timezone: str = "Asia/Shanghai"

    free_subscription_limit: int = 3
    free_daily_push_limit: int = 5
    member_subscription_limit: int = 30
    member_daily_push_limit: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def resolved_database_url(self) -> str:
        """自动检测数据库连接：优先使用 CloudBase MySQL 环境变量，其次使用配置的 database_url"""
        # CloudBase Run 会自动注入 TCB_MYSQL_* 环境变量
        tcb_uri = os.environ.get("TCB_MYSQL_CONNECTION_URI")
        if tcb_uri:
            # 替换 mysql:// 为 mysql+pymysql://
            if tcb_uri.startswith("mysql://"):
                return tcb_uri.replace("mysql://", "mysql+pymysql://", 1)
            return tcb_uri

        tcb_host = os.environ.get("TCB_MYSQL_HOST")
        if tcb_host:
            tcb_port = os.environ.get("TCB_MYSQL_PORT", "3306")
            tcb_user = os.environ.get("TCB_MYSQL_USER", "root")
            tcb_pass = os.environ.get("TCB_MYSQL_PASSWORD", "")
            tcb_db = os.environ.get("TCB_MYSQL_DATABASE", "")
            return f"mysql+pymysql://{tcb_user}:{tcb_pass}@{tcb_host}:{tcb_port}/{tcb_db}?charset=utf8mb4"

        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

