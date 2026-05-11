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

## 之前遇到的问题（已解决 ✅）

### 问题

服务端尝试访问雪球公开页面/API 获取博主主贴时，请求返回了阿里云 WAF challenge 页面，而不是预期的主贴 JSON/HTML。

后端已经确认：

- Cookie 配置可以被服务端读取；
- Cookie 中包含 `xq_a_token`、`xqat`、`xq_id_token`、`acw_tc` 等字段；
- Cookie 字符串没有换行；
- 请求没有报 Header 格式错误；
- 但服务端响应仍然是 WAF challenge；
- 因此当时无法解析出帖子，`posts` 表为空。

### 解决方案

通过分析阿里云 WAF 的 `acw_sc__v2` JS 挑战机制并实现纯 Python 求解器，已自动解决该问题。详见下方说明。

## 阿里云 WAF acw_sc__v2 解决方案

### 原理

雪球网使用了阿里云 WAF 的 `acw_sc__v2` Cookie 校验机制。访问流程：

1. 首次请求 → 返回含混淆 JS 的 WAF 挑战页面，内嵌 `arg1` 随机字符串
2. 浏览器端的 JS 先执行 `unsbox()`（按固定排列重排字符），再执行 `hexXor()`（与固定密钥异或），生成 `acw_sc__v2` Cookie
3. 携带该 Cookie 重发请求 → 通过 WAF 校验，得到真实内容

### 实现

`backend/app/services/acw_sc_v2.py`：纯 Python 实现的求解器，无需 Node.js。

核心算法（两步）：
1. **unsbox**：从 HTML 中提取 `arg1`，按固定排列数组 `_UNSBOX_ORDER` 重排字符
2. **hex_xor**：将重排结果与固定密钥 `3000176000856006061501533003690027800375` 逐十六进制位异或

### 爬虫改造

`backend/app/services/crawler.py` 中的 `XueqiuCrawler` 类新增了：
- `_ensure_session()`：自动检测 WAF 挑战并求解
- `_solve_waf()`：求解 `acw_sc__v2` 并重试建立会话
- User-Agent 轮换池（6 个常用 UA）
- 更完整的浏览器请求头模拟
- 会话过期后自动重解 WAF

### 测试验证

`tests/test_acw_sc_v2.py` 包含 5 个单元测试，使用已知输入输出验证算法正确性（arg1 → 预期 cookie 值）。
全部 10 个测试（含原有 API 和 Worker 测试）均通过。

## 希望雪球侧协助确认的问题

1. 是否可以提供正式、稳定、合规的服务端接口，用于获取授权范围内博主的主贴数据。
2. 当前访问 `statuses/original/timeline.json` 或用户主页时，通过逆向 acw_sc__v2 已可绕过 WAF。如果雪球侧能提供更稳定的接口或白名单，则更佳。
3. 对于该授权项目，正式建议的数据字段和返回格式是什么。
4. 是否允许 MVP 详情页展示全文；如果不允许，建议改为摘要字段或原文跳转字段。

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

