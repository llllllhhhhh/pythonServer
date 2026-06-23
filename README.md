# 学徒行 FastAPI 后端

为 `xuetuxing` 用户端和 `xuetuxingAdmin` 管理后台提供统一接口。

## 技术栈

- Python 3.12 / FastAPI / Uvicorn
- SQLAlchemy 2 异步 ORM
- MySQL 8.4 + `asyncmy`
- Redis 7.4（可选）
- WebSocket 实时客服
- Docker Compose（可选）

## 首次配置

复制环境变量文件：

Windows：

```powershell
copy .env.example .env
```

Linux：

```bash
cp .env.example .env
```

编辑 `.env`，至少确认 MySQL 参数：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=xuetuxing
MYSQL_PASSWORD=请填写你的数据库密码
MYSQL_DATABASE=xuetuxing
REDIS_ENABLED=false
```

数据库 `xuetuxing` 需要先存在；业务表不需要手动创建，后端启动时会自动创建和升级。

## Windows 原生启动

```powershell
cd xuetuxingServer
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到以下内容表示启动成功：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

不要重复启动多个 8000 端口进程。如果出现 `WinError 10048`，表示已有后端正在运行。

## Ubuntu / Linux 原生启动

```bash
cd /root/pythonServer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

正式部署建议使用 systemd 或 Supervisor 保持进程运行，不要只在 SSH 窗口前台运行。

## Docker Compose 启动

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f api
```

如果服务器无法访问 Docker Hub，可以在能联网的电脑上保存镜像并上传：

```bash
docker save -o python3.12-slim.tar python:3.12-slim
docker save -o mysql8.4.tar mysql:8.4
docker save -o redis7.4.tar redis:7.4-alpine
```

服务器导入：

```bash
docker load -i python3.12-slim.tar
docker load -i mysql8.4.tar
docker load -i redis7.4.tar
docker compose up -d --build
```

Python 基础镜像只包含 Python，不包含 `requirements.txt` 里的项目依赖。离线构建时还需要准备 `wheels` 目录，或者直接使用原生 Python 虚拟环境运行。

## 启动验证

- 健康检查：`http://127.0.0.1:8000/health`
- Swagger：`http://127.0.0.1:8000/docs`
- OpenAPI：`http://127.0.0.1:8000/openapi.json`

默认管理员：

- 账号：`13800000000`
- 密码：`admin123456`

演示用户：

- 账号：`17600008032`
- 密码：`12345678`

生产环境必须修改管理员密码和 `ADMIN_API_KEY`。

## 学习商城与支付

后端启动后自动创建：

- `study_products`：付费社群、长期套餐和资料包。
- `study_contents`：课程、资料、模考和服务内容。
- `study_orders`：学习服务订单与支付状态。
- `user_entitlements`：用户已购权益。
- `learning_profiles`：目标考试、阶段、学习时长和打卡档案。

开发环境支付流程：

1. 用户创建学习订单。
2. 调用模拟支付接口。
3. 订单变为 `paid`。
4. 自动生成用户权益和学习档案。

模拟支付只允许 `ENVIRONMENT=development` 或 `test`，生产环境会拒绝模拟支付。

## 微信支付配置

在 `.env` 中配置：

```env
WECHAT_APP_ID=微信小程序AppID
WECHAT_MCH_ID=微信支付商户号
WECHAT_API_V3_KEY=微信支付APIv3密钥
WECHAT_NOTIFY_URL=https://api.example.com/api/v1/commerce/payments/wechat/notify
```

还需要准备商户 API 证书和平台证书，并在服务端实现统一下单签名、支付回调验签和退款流程。没有这些微信商户资料时，项目只能使用开发环境模拟支付，不会发生真实扣款。

## 主要接口

| 方法 | 地址 | 用途 |
|---|---|---|
| POST | `/api/v1/auth/register` | 提交用户注册申请 |
| POST | `/api/v1/auth/login` | 用户登录 |
| GET/PATCH | `/api/v1/admin/registrations` | 注册审核 |
| GET/PATCH | `/api/v1/admin/users` | 用户管理 |
| GET | `/api/v1/public/study/products` | 获取已上架学习产品 |
| GET | `/api/v1/public/study/products/{id}` | 学习产品详情与试看内容 |
| POST | `/api/v1/commerce/orders` | 创建学习订单 |
| POST | `/api/v1/commerce/orders/{id}/pay/mock` | 开发环境模拟支付 |
| GET | `/api/v1/commerce/me/learning-center` | 用户学习中心与权益 |
| POST | `/api/v1/commerce/me/check-in` | 每日学习打卡 |
| GET/POST/PUT | `/api/v1/admin/study/products` | 学习产品管理 |
| GET | `/api/v1/admin/study/orders` | 学习订单管理 |
| GET/PUT | `/api/v1/admin/decoration/draft` | 装修草稿 |
| POST | `/api/v1/admin/decoration/publish` | 发布装修配置 |
| GET/POST/PUT | `/api/v1/admin/routes` | 旅行路线管理 |
| GET/PUT | `/api/v1/admin/points/rule` | 积分规则 |

完整字段和在线调试请打开 Swagger。

## 三端启动顺序

1. 启动 MySQL。
2. 启动 FastAPI 后端，验证 `/health`。
3. 启动 `xuetuxingAdmin` 管理端。
4. 使用 HBuilderX 运行 `xuetuxing` 用户端。

修改后端 Python 代码后必须重启 FastAPI；修改管理端代码后，开发模式会自动更新，生产部署则需要重新构建 `dist`。

## 正式部署注意事项

- 使用 Nginx 反向代理 FastAPI。
- 配置 HTTPS 域名和证书。
- 云服务器安全组只开放必要端口，MySQL 和 Redis 不要直接暴露公网。
- 微信小程序只连接 HTTPS 合法域名。
- 配置 CORS 为真实管理端域名。
- 将 `.env`、支付证书和数据库密码排除在版本控制之外。
- 定期备份 MySQL 数据库和上传文件目录。
