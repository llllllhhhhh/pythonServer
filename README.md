# 学徒行 FastAPI 后端

为 `xuetuxing` 用户端和 `xuetuxingAdmin` 管理后台提供统一接口。

## 技术栈

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2 异步 ORM
- MySQL 8.4，异步驱动 `asyncmy`
- Redis 7.4，缓存已发布装修配置
- Docker Compose

## 一键启动（推荐）

安装 Docker Desktop 后，在本目录执行：

```bash
docker compose up -d --build
```

启动完成后：

- API：`http://127.0.0.1:8000`
- Swagger 接口文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- MySQL：`127.0.0.1:3306`
- Redis：`127.0.0.1:6379`

首次启动会自动创建数据表并写入演示数据。

Redis 是可选项。本机没有 Redis 时，在 `.env` 中设置 `REDIS_ENABLED=false`，系统会直接从 MySQL 读取数据。

## 本机 Python 启动

先自行启动 MySQL 和 Redis，然后：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

项目包含 `cryptography`，用于兼容 MySQL 8 默认的 `caching_sha2_password` 身份认证方式。如果遇到相关缺失提示，请重新执行 `python -m pip install -r requirements.txt`。

## 三端联通

1. 启动本后端，确认 `/health` 返回 `status: ok`。
2. 运行 `xuetuxingAdmin`，默认连接 `http://127.0.0.1:8000/api/v1`。
3. 在管理后台进入“页面装修”，修改后点击“发布到小程序”。
4. 重启或刷新 `xuetuxing` 用户端，首页会读取最新发布配置；旅行页会读取 MySQL 中已上架路线。

管理端开发密钥默认为 `xuetuxing-dev-key`，由请求头 `X-Admin-Key` 传递。生产部署务必修改 `.env` 中的 `ADMIN_API_KEY`，并替换为完整的账号、JWT 与权限体系。

## 核心接口

| 方法 | 地址 | 用途 |
|---|---|---|
| GET | `/api/v1/public/config` | 用户端读取已发布装修配置 |
| GET | `/api/v1/public/routes` | 用户端读取已上架路线 |
| GET | `/api/v1/public/points/rule` | 用户端读取积分规则 |
| GET/PUT | `/api/v1/admin/decoration/draft` | 读取、保存装修草稿 |
| POST | `/api/v1/admin/decoration/publish` | 发布装修配置并清除 Redis 缓存 |
| GET/POST/PUT | `/api/v1/admin/routes` | 旅行路线管理 |
| GET/PUT | `/api/v1/admin/points/rule` | 积分规则管理 |
| GET/PATCH | `/api/v1/admin/orders` | 订单查询与审核 |
| GET/PATCH | `/api/v1/admin/invites` | 邀请关系与冻结处理 |

完整请求参数和在线调试请使用 Swagger 文档。

## 微信小程序注意事项

本地开发可在微信开发者工具中关闭域名校验。正式发布时需要：

- 将 API 部署到具有 HTTPS 的公网域名；
- 在微信公众平台配置该域名为 `request` 合法域名；
- 修改 `xuetuxing/utils/api.js` 中的默认 API 地址，或通过 `uni.setStorageSync('apiBaseUrl', 'https://你的域名/api/v1')` 设置。
