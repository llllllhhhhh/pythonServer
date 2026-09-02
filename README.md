# 学徒行 FastAPI 后端

这是学徒行项目的统一后端，负责用户端、管理后台、商户端之间的数据通信。

## 技术栈

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.x 异步 ORM
- MySQL 8.x
- Redis，必需。标准订单创建依赖 Redis 做幂等锁和库存预扣，开发与生产环境都必须启用。
- WebSocket 实时客服
- OBS / 本地上传存储

## 目录说明

```text
xuetuxingServer
├─ app
│  ├─ api              接口路由
│  ├─ core             配置、数据库、缓存、存储
│  ├─ models           SQLAlchemy 数据模型
│  ├─ schemas          Pydantic 入参出参
│  ├─ services         业务服务
│  ├─ seed.py          初始数据
│  └─ main.py          FastAPI 入口
├─ requirements.txt
├─ .env.example
└─ docker-compose.yml
```

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

重点检查 `.env`：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=xuetuxing
MYSQL_PASSWORD=你的数据库密码
MYSQL_DATABASE=xuetuxing

# Docker Compose 数据库使用独立变量，避免与上面的本机连接串用
COMPOSE_MYSQL_ROOT_PASSWORD=容器数据库root强密码
COMPOSE_MYSQL_USER=xuetuxing
COMPOSE_MYSQL_PASSWORD=容器数据库应用账号强密码
COMPOSE_MYSQL_DATABASE=xuetuxing

REDIS_ENABLED=true
REDIS_URL=redis://127.0.0.1:6379/0

CORS_ORIGINS=http://localhost:5178,http://127.0.0.1:5178
# 仅信任实际 Nginx / 负载均衡器的 IP 或 CIDR
TRUSTED_PROXY_IPS=127.0.0.1,::1

STORAGE_DRIVER=obs
OBS_ACCESS_KEY=你的华为云 OBS Access Key Id
OBS_SECRET_KEY=你的华为云 OBS Secret Access Key
OBS_BUCKET=xuetuxing
OBS_REGION=cn-north-4
OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com
```

先创建 MySQL 数据库 `xuetuxing`，再执行 Alembic。API 启动时不会建表或改表；数据库不是当前版本时会拒绝启动。

## Windows 本地启动

```powershell
cd outputs\xuetuxingServer
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m app.db_migrate
python -m alembic check
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到下面内容就是成功：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

如果出现 `WinError 10048` 或 `address already in use`，说明 8000 端口已经有后端在运行，不要重复启动。

## Linux / Ubuntu 原生部署

```bash
cd /root/pythonServer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.db_migrate
python -m alembic check
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

正式部署建议使用 systemd 托管：

```ini
[Unit]
Description=Xuetuxing FastAPI Server
After=network.target mysql.service

[Service]
WorkingDirectory=/root/pythonServer
ExecStart=/root/pythonServer/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

保存为 `/etc/systemd/system/xuetuxing-api.service` 后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable xuetuxing-api
sudo systemctl start xuetuxing-api
sudo systemctl status xuetuxing-api
```

非 Docker 部署还必须单独托管 RQ Worker。保存为 `/etc/systemd/system/xuetuxing-worker.service`：

```ini
[Unit]
Description=Xuetuxing RQ Worker
After=network.target mysql.service redis-server.service
Requires=redis-server.service

[Service]
WorkingDirectory=/root/pythonServer
EnvironmentFile=/root/pythonServer/.env
ExecStart=/root/pythonServer/.venv/bin/rq worker --with-scheduler --url redis://127.0.0.1:6379/0 xuetuxing-orders
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xuetuxing-worker
sudo systemctl status xuetuxing-worker
```

## Docker 部署

Compose 会按顺序启动 MySQL/Redis、执行一次性 `migrate` 容器，再启动 API 和 RQ Worker。`migrate` 必须以退出码 0 结束，API 才会启动。

如果 API 前面有 Nginx 或负载均衡器，需要在 `.env` 的 `TRUSTED_PROXY_IPS` 中填写该代理连接 API 时使用的真实源 IP/CIDR。Nginx 与 API 都在宿主机时通常为 `127.0.0.1,::1`；代理位于 Docker 网络时填写对应网段，例如 `172.20.0.0/16`。禁止配置 `0.0.0.0/0`，否则客户端可伪造转发头绕过登录限流。

服务器能访问 Docker Hub 时：

```bash
docker compose up -d --build
docker compose ps -a
docker compose logs -f api worker
```

正常状态应满足：

- `migrate` 为 `Exited (0)`。
- `mysql`、`redis`、`api` 为 `healthy`。
- `worker` 为 `Up`，`rq info` 能看到监听 `xuetuxing-orders` 的 Worker。

如果数据库来自没有 `alembic_version` 的旧版本，必须先做完整备份。开发环境会自动接管受支持的旧结构；生产环境需要临时设置 `ALLOW_LEGACY_SCHEMA_ADOPTION=true` 完成一次迁移，确认 `alembic check` 通过后立即恢复为 `false`。含未知表或不符合旧版结构指纹的数据库会被拒绝，不会被盲目标记为最新版本。

服务器不能拉镜像时，在本地先保存镜像：

```bash
docker save -o python3.12-slim.tar python:3.12-slim
docker save -o mysql8.4.tar mysql:8.4
docker save -o redis7.4.tar redis:7.4-alpine
```

上传到服务器后导入：

```bash
docker load -i python3.12-slim.tar
docker load -i mysql8.4.tar
docker load -i redis7.4.tar
docker compose up -d --build
```

注意：Python 基础镜像只包含 Python，不包含 `requirements.txt` 里的依赖。离线构建时还需要准备 wheels 依赖包，或者直接用原生 Python 虚拟环境启动。

## Nginx 推荐配置

管理后台静态文件放到 `/var/www/admin`，后端跑在 `127.0.0.1:8000`：

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name 113.44.149.128 goxuetuxing.com www.goxuetuxing.com;

    root /var/www/admin;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/v1/support/ws/ {
        proxy_pass http://127.0.0.1:8000/api/v1/support/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    location /uploads/ {
        proxy_pass http://127.0.0.1:8000/uploads/;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
    }

    location /openapi.json {
        proxy_pass http://127.0.0.1:8000/openapi.json;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

修改后：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 启动验证

- 健康检查：`http://127.0.0.1:8000/health`
- Swagger：`http://127.0.0.1:8000/docs`
- 线上健康检查：`http://113.44.149.128/health`

生产环境不会自动创建账号。仅本地开发需要演示数据时显式执行：

```bash
python -m app.cli seed-development --admin-password "仅限本地的测试密码"
```

开发 seed 默认创建的管理账号：

- 账号：`13800000000`
- 密码：`admin123456`

开发 seed 创建的演示用户：

- 账号：`17600008032`
- 密码：`12345678`

生产环境使用 `python -m app.cli bootstrap-admin` 显式创建首个管理员，且不得运行 `seed-development`。管理接口只接受管理员登录会话，不支持固定 `ADMIN_API_KEY` 旁路。

## 核心功能接口

| 模块 | 接口 | 说明 |
|---|---|---|
| 登录注册 | `/api/v1/auth/register` | 用户提交注册申请 |
| 登录注册 | `/api/v1/auth/login` | 用户登录 |
| 管理端注册审核 | `/api/v1/admin/registrations` | 审核注册用户 |
| 用户管理 | `/api/v1/admin/users` | 用户列表、密码、余额、状态 |
| 装修 | `/api/v1/admin/decoration/draft` | 装修草稿 |
| 装修 | `/api/v1/admin/decoration/publish` | 发布装修到用户端 |
| 旅行路线 | `/api/v1/admin/routes` | 路线管理、上下架 |
| 人工定制 | `/api/v1/custom-travel/requests` | 用户提交/查看人工定制需求 |
| 人工定制审核 | `/api/v1/admin/custom-travel/requests` | 管理后台审核并填写方案 |
| 学习产品 | `/api/v1/public/study/products` | 用户端学习服务列表 |
| 学习订单 | `/api/v1/commerce/standard-orders` | 余额或微信支付购买学习产品 |
| 订单退款 | `/api/v1/admin/commerce/orders/{order_no}/refund` | 管理员退款（幂等，回补余额与库存） |
| 邀请积分补发 | `/api/v1/admin/invites/backfill-rewards` | 规则开启后补发历史邀请积分 |
| 偏好事件 | `/api/v1/public/preferences/events` | 用户偏好采集（登录用户强制归属，游客按 IP 限流） |
| 商户端 | `/api/v1/merchant/login` | 学校商户登录 |
| 客服 | `/api/v1/support/ws/{id}` | WebSocket 实时客服（token 走 Sec-WebSocket-Protocol 子协议） |
| 图片资源 | `/api/v1/admin/assets/images` | 管理端查看上传图片 |

完整字段请打开 Swagger 查看。

## 学习订单系统

标准学习订单使用 `commerce_orders` 和 `commerce_order_items` 两张表，用户端支持余额支付和微信小程序 JSAPI 支付：

1. `POST /api/v1/commerce/standard-orders` 创建订单。
2. `POST /api/v1/commerce/standard-orders/{order_no}/pay/balance` 扣余额并支付。
   微信支付则调用 `POST /api/v1/commerce/standard-orders/{order_no}/pay/wechat` 获取 `uni.requestPayment` 参数。
3. 微信服务器向 `POST /api/v1/commerce/wechat/notify` 发回支付通知；后端验签、解密并核对商户号、AppID、订单号和金额后入账。
4. 余额支付写入 `wallet_transactions` 支出流水；微信支付保存微信交易号。
5. 后端写入 `user_entitlements`，学习中心据此展示已购权益。
6. 管理端 `/api/v1/admin/commerce/orders` 和商户端 `/api/v1/merchant/commerce/orders` 同步查看订单。

关键保护：

- 创建订单要求传 `idempotency_key`。
- 后端先查数据库中同用户同 `idempotency_key` 的历史订单，再用 Redis `SETNX` 防并发创建。
- `commerce_orders` 对 `(user_id, idempotency_key)` 有唯一约束，作为 Redis 并发锁之外的数据库兜底。
- 支付时会锁定订单行和用户余额行，重复请求同一订单只会返回已支付订单，不会重复扣款。
- 自动取消未支付订单时也锁订单行，避免支付和取消同时改状态。
- 有限库存商品会先用 Redis 预扣，再在 MySQL 中扣减；失败会回滚 Redis 库存。
- 未支付订单默认 30 分钟超时，配置项是 `.env` / `Settings.order_payment_timeout_minutes`。

### 微信支付配置

微信支付金额以整数“分”为单位，真实支付最低测试金额为 `0.01 元`，不能使用 `0.001 元`。小程序 AppID 必须已绑定到微信支付商户号。

```env
WECHAT_MCH_ID=商户号
WECHAT_API_V3_KEY=32位APIv3密钥
WECHAT_NOTIFY_URL=https://你的域名/api/v1/commerce/wechat/notify
WECHAT_MERCHANT_SERIAL_NO=商户API证书序列号
WECHAT_MERCHANT_PRIVATE_KEY_PATH=C:/绝对路径/apiclient_key.pem
WECHAT_PAY_PUBLIC_KEY_ID=PUB_KEY_ID开头的微信支付公钥ID
WECHAT_PAY_PUBLIC_KEY_PATH=C:/绝对路径/wechatpay_pub.pem
```

`apiclient_key.pem` 是商户 API 证书私钥；`wechatpay_pub.pem` 是微信支付公钥，两者不是同一个文件。本项目仅使用微信支付公钥模式，回调和统一下单响应的 `Wechatpay-Serial` 必须与 `WECHAT_PAY_PUBLIC_KEY_ID` 一致。私钥只能放在后端服务器，禁止放入小程序、管理后台或 Git。回调地址必须是公网可访问的 HTTPS 地址，服务器时间需启用 NTP 同步。

订单问题排查：

```sql
-- 1. 查订单主表
SELECT id, order_no, user_id, school_id, total_amount, payable_amount,
       payment_status, status, idempotency_key, transaction_id,
       paid_at, canceled_at, cancel_reason, created_at
FROM commerce_orders
WHERE order_no = '你的订单号';

-- 2. 查订单明细
SELECT order_no, product_id, product_name, quantity, total_amount,
       stock_deducted, status
FROM commerce_order_items
WHERE order_no = '你的订单号';

-- 3. 查余额流水，正常同一个订单只应有一条 purchase 支出
SELECT transaction_no, direction, amount, balance_before, balance_after,
       biz_type, biz_no, created_at
FROM wallet_transactions
WHERE biz_no = '你的订单号';

-- 4. 查权益是否发放
SELECT id, user_id, product_id, order_id, entitlement_type, starts_at, expires_at, status
FROM user_entitlements
WHERE order_id = 订单主表id;
```

常见现象：

- 用户说扣款了但学习中心没权益：先查 `wallet_transactions`，再查 `user_entitlements`；如果有流水没权益，看后端日志 `order_paid` 附近异常。
- 用户重复点支付：查同一个 `order_no` 是否只有一条 `wallet_transactions.biz_no`；当前后端支付接口会防重复扣款。
- 出现多张待支付订单：看 `idempotency_key` 是否每次都不同；用户端同一次收银台支付会复用同一个 key，重新进入收银台会生成新 key。
- 创建订单返回 `REDIS_REQUIRED`：检查 Redis 是否启动、`.env` 中 `REDIS_ENABLED=true` 和 `REDIS_URL` 是否正确。
- 订单一直 pending：检查支付接口是否调用成功、余额是否足够、后端日志是否有 `INSUFFICIENT_BALANCE` 或 `ORDER_NOT_PAYABLE`。

## 常见问题

### 1. `cryptography package is required`

MySQL 8 默认认证方式需要 `cryptography`：

```bash
python -m pip install cryptography
```

### 2. 端口被占用

Windows：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

Linux：

```bash
ss -lntp | grep 8000
```

### 3. 后台接口 404

通常是服务器上跑的不是最新后端。重启 FastAPI，再打开 `/docs` 检查接口是否存在。

### 4. 客服 WebSocket 连接不上

检查 Nginx 是否配置了：

```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

### 5. 图片上传不展示

如果使用 OBS：

- `.env` 中 `STORAGE_DRIVER=obs`
- OBS AK/SK、桶名、区域正确
- 桶和私密对象保持私有 ACL
- 公开封面使用允许公开的资源来源；合同、签名、客服附件通过后端鉴权或短期签名 URL 读取

## 代码检查

```bash
python -m compileall -q app tests
python -m unittest discover -s tests -v
python -m alembic check
docker compose config --quiet
```
