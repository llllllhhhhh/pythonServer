# 学徒行 FastAPI 后端

这是学徒行项目的统一后端，负责用户端、管理后台、商户端之间的数据通信。

## 技术栈

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.x 异步 ORM
- MySQL 8.x
- Redis，可选
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

REDIS_ENABLED=false
REDIS_URL=redis://127.0.0.1:6379/0

CORS_ORIGINS=http://localhost:5178,http://127.0.0.1:5178,http://113.44.149.128

STORAGE_DRIVER=obs
OBS_ACCESS_KEY=你的华为云 OBS Access Key Id
OBS_SECRET_KEY=你的华为云 OBS Secret Access Key
OBS_BUCKET=xuetuxing
OBS_REGION=cn-north-4
OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com
OBS_PUBLIC_BASE_URL=https://xuetuxing.obs.cn-north-4.myhuaweicloud.com
```

只需要先创建 MySQL 数据库 `xuetuxing`，业务表会在后端启动时自动创建和升级。

## Windows 本地启动

```powershell
cd outputs\xuetuxingServer
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
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

## Docker 部署

服务器能访问 Docker Hub 时：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f api
```

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

默认管理账号：

- 账号：`13800000000`
- 密码：`admin123456`

演示用户：

- 账号：`17600008032`
- 密码：`12345678`

正式环境必须修改默认管理员密码和 `ADMIN_API_KEY`。

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
| 学习订单 | `/api/v1/commerce/standard-orders` | 余额购买学习产品 |
| 商户端 | `/api/v1/merchant/login` | 学校商户登录 |
| 客服 | `/api/v1/support/ws/{id}` | WebSocket 实时客服 |
| 图片资源 | `/api/v1/admin/assets/images` | 管理端查看上传图片 |

完整字段请打开 Swagger 查看。

## 学习订单系统

标准学习订单使用 `commerce_orders` 和 `commerce_order_items` 两张表，用户端走余额支付：

1. `POST /api/v1/commerce/standard-orders` 创建订单。
2. `POST /api/v1/commerce/standard-orders/{order_no}/pay/balance` 扣余额并支付。
3. 后端写入 `wallet_transactions` 支出流水。
4. 后端写入 `user_entitlements`，学习中心据此展示已购权益。
5. 管理端 `/api/v1/admin/commerce/orders` 和商户端 `/api/v1/merchant/commerce/orders` 同步查看订单。

关键保护：

- 创建订单要求传 `idempotency_key`。
- 后端先查数据库中同用户同 `idempotency_key` 的历史订单，再用 Redis `SETNX` 防并发创建。
- 支付时会锁定订单行和用户余额行，重复请求同一订单只会返回已支付订单，不会重复扣款。
- 自动取消未支付订单时也锁订单行，避免支付和取消同时改状态。
- 有限库存商品会先用 Redis 预扣，再在 MySQL 中扣减；失败会回滚 Redis 库存。
- 未支付订单默认 30 分钟超时，配置项是 `.env` / `Settings.order_payment_timeout_minutes`。

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
- 桶 ACL 或后端代理接口能读取图片
- 管理端和用户端使用后端 `/api/v1/public/assets/...` 代理地址展示图片

## 代码检查

```bash
python -m py_compile app/main.py app/api/*.py app/models/entities.py app/core/database.py
```
