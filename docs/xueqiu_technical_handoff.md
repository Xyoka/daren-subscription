# 达人订阅 MVP - 雪球侧技术协助说明

## 项目背景

「达人订阅」是一个获得雪球官方授权/背书背景下开发的信息订阅提醒 MVP。目标是为用户提供软件外订阅提醒能力：

```text
预置雪球博主 -> 服务端获取最新主贴 -> 去重入库 -> 匹配用户订阅关系 -> 微信小程序订阅消息推送 -> 小程序详情页查看
```

当前项目仅用于小范围体验版内测，不做公开商业化发布。

## 当前技术栈

- 后端：FastAPI
- 数据库：PostgreSQL / 本地开发 SQLite
- 缓存/队列预留：Redis
- 前端：微信小程序原生
- 管理后台：FastAPI + Jinja2
- 部署：Docker Compose

## 当前遇到的问题

服务端尝试访问雪球公开页面/API 获取博主主贴时，请求返回了阿里云 WAF challenge 页面，而不是预期的主贴 JSON/HTML。

后端已经确认：

- Cookie 配置可以被服务端读取；
- Cookie 中包含 `xq_a_token`、`xqat`、`xq_id_token`、`acw_tc` 等字段；
- Cookie 字符串没有换行；
- 请求没有报 Header 格式错误；
- 但服务端响应仍然是 WAF challenge；
- 因此当前无法解析出帖子，`posts` 表为空。

后台抓取日志中的典型错误：

```text
Xueqiu returned an Aliyun WAF challenge instead of post content.
Configure a valid XUEQIU_COOKIE or verify server-side access.
```

## 希望雪球侧协助确认的问题

1. 是否可以提供正式、稳定、合规的服务端接口，用于获取授权范围内博主的主贴数据。
2. 如果必须通过现有页面/API 访问，是否可以提供：
   - 测试环境；
   - 服务端白名单；
   - 专用访问凭证；
   - 推荐请求域名/API；
   - 必要请求头规范；
   - 合规频率限制。
3. 当前访问 `statuses/original/timeline.json` 或用户主页时返回 WAF challenge，是否属于未加入白名单或服务端访问方式不被允许。
4. 对于该授权项目，正式建议的数据字段和返回格式是什么。
5. 是否允许 MVP 详情页展示全文；如果不允许，建议改为摘要字段或原文跳转字段。

## 当前后端相关代码位置

- 抓取逻辑：`backend/app/services/crawler.py`
- 抓取调度：`backend/app/services/worker.py`
- 数据模型：`backend/app/models.py`
- 管理后台：`backend/app/routers/admin.py`
- API：`backend/app/routers/api.py`

## 本地启动方式

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

默认后台地址：

```text
http://127.0.0.1:8000/admin
```

默认本地账号：

```text
admin / admin
```

## 注意事项

请勿通过聊天、邮件或代码仓库发送真实 Cookie、微信 AppSecret、生产数据库、用户数据或 `.env` 文件。

需要共享配置时，请使用 `backend/.env.example`，由雪球侧技术人员在自己的环境中填写正式测试凭证。

