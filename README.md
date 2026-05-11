# 达人订阅

微信小程序体验版 MVP：订阅预置雪球博主，新主贴抓取后通过小程序订阅消息提醒，详情页展示内测全文并支持复制原文链接。

## 组成

- `backend/`：FastAPI API、Worker、极简管理后台。
- `miniprogram/`：微信小程序原生端代码。
- `docker-compose.yml`：PostgreSQL、Redis、API、Worker 本地/服务器部署编排。

## 本地后端启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

默认使用 `sqlite:///./darensub.db`，便于本地开发。管理后台地址为 `http://127.0.0.1:8000/admin`，默认账号密码见 `backend/.env.example`。

## Docker 启动

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

生产部署前请修改 `SECRET_KEY`、管理员密码、微信小程序配置、HTTPS 域名。

> ⚠️ **雪球 WAF 说明**：雪球网使用了阿里云 WAF 防护。爬虫默认使用 httpx，若遇到新版 WAF 挑战会**自动降级到 Playwright 无头浏览器**解决。首次使用需先安装浏览器：
> ```bash
> cd backend && .venv/bin/playwright install chromium
> ```

## P0 验证顺序

1. 在后台新增 2-3 个雪球博主，执行抓取测试，确认帖子 ID、正文、发布时间、原文链接可解析。
2. 在微信后台申请订阅消息模板，填写 `WECHAT_TEMPLATE_ID`，关闭 `WECHAT_DRY_RUN` 后测试真实推送。
3. 用体验版小程序验证登录、白名单、订阅授权、推送点击进入详情页、复制原文链接。

