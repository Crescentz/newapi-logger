# newapi-logger — API 对话日志系统 v2.0

> 在 newapi 前面插一层透明代理，记录**所有 API 调用**的完整内容：
> LLM 对话、词向量、重排序、多模态、文生图 — 全部记录。
> **不改 newapi 一行代码**，日志服务宕机时原服务不受影响。

---

## 目录

- [1. 这是什么](#1-这是什么)
- [2. 技术架构](#2-技术架构)
- [3. 准备工作：MySQL Docker 部署](#3-准备工作mysql-docker-部署)
- [4. 准备工作：Nginx Docker 部署](#4-准备工作nginx-docker-部署)
- [5. 部署 api-logger](#5-部署-api-logger)
- [6. 验证与测试](#6-验证与测试)
- [7. 日志查看与数据分析](#7-日志查看与数据分析)
- [8. 日常运维](#8-日常运维)
- [9. 故障排查](#9-故障排查)
- [10. 离线环境导入](#10-离线环境导入)

---

## 1. 这是什么

你的环境：newapi（端口 55000）管理令牌，调用 vllm（55001/55002/55003）跑大模型，bge 做词向量（55006），rerank 做重排序（55006），通义-Z-imate-turbo 做多模态（54001）。

**问题**：newapi 自带的日志只有简单的调用次数，看不到用户到底问了什么、模型回了什么。

**解决**：在 nginx 和 newapi 之间插一个透明代理，把每次 API 调用的完整信息记录下来。

### 关于令牌追踪

```
客户端请求
  │  Authorization: Bearer sk-abc123def456    ← newapi的令牌
  ▼
Nginx ──► api-logger（在这里抓到令牌！）──► newapi（验证令牌，转发给模型）
              │
              ├─ token_full = sk-abc123def456     ← 完整密钥存库
              └─ token_name = "张三的令牌 (sk-abc...f456)"  ← 自动读newapi SQLite拿名称
```

**两层追踪**：
1. `token_full`：完整令牌密钥，直接和 newapi 令牌管理页面对应
2. `token_name`：自动从 newapi 的 **SQLite 数据库**查出令牌的人类可读名称

**如何工作**：newapi（`calciumion/new-api`）使用 SQLite 存储数据（`./data/one-api.db`）。api-logger 通过 Docker volume 以只读方式挂载该目录，直接读取 `tokens` 表中的 `key → name` 映射。

> ⚠️ 需在 `.env` 中配置 `NEWAPI_DATA_DIR` 指向 newapi 的 data 目录。不配也能用，`token_name` 显示脱敏密钥。

**记录什么**：

| 记录项 | 举例 |
|--------|------|
| 是哪个用户 | 通过令牌（token）精确追踪，对应 newapi 的令牌管理 |
| 用户问了什么 | 完整 prompt / messages / 多轮对话历史 |
| 模型回答了什么 | 完整 response / choices |
| 思考过程 | reasoning_content（reasoning 模型） |
| 用了多少 token | prompt_tokens + completion_tokens |
| 调了什么模型 | chat / embedding / rerank / 图像生成 |
| 什么时候 | 精确到毫秒的时间戳 |
| 延迟多久 | 毫秒级响应时间 |

**不改代码**：newapi 和 vllm 完全不动。日志服务独立启停，宕机时 nginx 自动绕过。

---

## 2. 技术架构

```
                        ┌──────────────────┐
  用户/应用 ──────────▶  │  Nginx (端口80)   │
                        └────────┬─────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼ (日志宕机时)
           ┌──────────────┐           ┌──────────────┐
           │ api-logger   │──────────▶│   newapi     │──▶ 多个 vllm
           │   (55020)    │  透明转发  │   (55000)    │    (55001-3)
           └──────┬───────┘           └──────────────┘    bge (55006)
                  │                                       rerank (55006)
                  │ 异步写入                               通义 (54001)
                  ▼
           ┌──────────┐
           │  MySQL   │
           └──────────┘
```

**端口分配**：

| 服务 | 容器名 | 内部端口 | 宿主机端口 | 说明 |
|------|--------|---------|-----------|------|
| Nginx | nginx | 55010 | 55010 | 对外入口 |
| api-logger | api-logger | 55020 | 55020 | 日志代理（内部） |
| newapi | newapi | 55000 | 55000 | API 管理 |
| vllm-1 | vllm-1 | 8000 | 55001 | 大模型 1 |
| vllm-2 | vllm-2 | 8000 | 55002 | 大模型 2 |
| vllm-3 | vllm-3 | 8000 | 55003 | 大模型 3 |
| bge | bge | 8000 | 55006 | 词向量 |
| rerank | rerank | 8000 | 55006 | 重排序 |
| 通义 | tongyi | 8000 | 54001 | 多模态 |
| MySQL | mysql | 3306 | 33060 | 日志存储 |

---

## 3. 准备工作：MySQL Docker 部署

> 如果你已经有 MySQL 在用，跳到第 4 步。以下是全新部署 MySQL 的完整步骤。

### 3.1 拉取 MySQL 镜像

```bash
docker pull mysql:8.0
```

### 3.2 创建数据目录

```bash
# 在宿主机上创建持久化目录
mkdir -p /data/mysql/data
mkdir -p /data/mysql/conf
```

### 3.3 启动 MySQL 容器

```bash
docker run -d \
  --name mysql \
  --restart unless-stopped \
  --network newapi-net \
  -p 33060:3306 \
  -v /data/mysql/data:/var/lib/mysql \
  -v /data/mysql/conf:/etc/mysql/conf.d \
  -e MYSQL_ROOT_PASSWORD=设置你的密码 \
  -e MYSQL_DATABASE=newapi_logs \
  -e TZ=Asia/Shanghai \
  mysql:8.0 \
  --character-set-server=utf8mb4 \
  --collation-server=utf8mb4_unicode_ci \
  --default-authentication-plugin=mysql_native_password
```

**参数说明**：

| 参数 | 说明 |
|------|------|
| `--name mysql` | 容器名称 |
| `--restart unless-stopped` | 自动重启 |
| `--network newapi-net` | 加入 Docker 网络（确保和其他服务互通） |
| `-p 33060:3306` | 宿主机 33060 → 容器 3306（避免冲突） |
| `-v /data/mysql/data:/var/lib/mysql` | 数据持久化 |
| `-e MYSQL_ROOT_PASSWORD=...` | root 密码 |
| `-e MYSQL_DATABASE=newapi_logs` | 自动创建数据库 |

### 3.4 验证 MySQL 可用

```bash
# 从宿主机连接（端口 33060）
mysql -h 127.0.0.1 -P 33060 -u root -p

# 或者进入容器
docker exec -it mysql mysql -u root -p

# 查看数据库
SHOW DATABASES;
# 应该看到 newapi_logs
```

---

## 4. 准备工作：Nginx Docker 部署

> 如果你已经有 Nginx 在用，直接跳到 4.5 修改配置。

### 4.1 拉取 Nginx 镜像

```bash
docker pull nginx:latest
```

### 4.2 创建 Nginx 配置目录

```bash
mkdir -p /data/nginx/conf.d
mkdir -p /data/nginx/logs
```

### 4.3 创建主配置文件

创建 `/data/nginx/nginx.conf`：

```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 2048;
    use epoll;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';

    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;

    # 包含站点配置
    include /etc/nginx/conf.d/*.conf;
}
```

### 4.4 创建站点配置

创建 `/data/nginx/conf.d/newapi.conf`（**核心配置**）：

```nginx
# ===== upstream：主节点 = 日志代理，备用 = 直连 newapi =====
upstream newapi_backend {
    server api-logger:55020 max_fails=3 fail_timeout=30s;
    server newapi:55000 backup;

    keepalive 128;
    keepalive_timeout 60s;
    keepalive_requests 2000;
}

server {
    listen 55010;
    server_name _;

    client_max_body_size 200m;

    # 超时设置（流式响应 / 长思考需要）
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_connect_timeout 10s;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 关键！关闭缓冲才能转发流式响应
    proxy_buffering off;
    proxy_request_buffering off;

    location / {
        proxy_pass http://newapi_backend;

        add_header Access-Control-Allow-Origin * always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type" always;
        if ($request_method = OPTIONS) {
            return 204;
        }
    }
}
```

### 4.5 启动 Nginx

```bash
docker run -d \
  --name nginx \
  --restart unless-stopped \
  --network newapi-net \
  -p 80:80 \
  -v /data/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \
  -v /data/nginx/conf.d:/etc/nginx/conf.d:ro \
  -v /data/nginx/logs:/var/log/nginx \
  nginx:latest
```

### 4.6 验证 Nginx

```bash
# 检查配置是否正确
docker exec nginx nginx -t

# 如果报错，查看错误日志
docker logs nginx --tail 20

# 测试访问（应该能转发到 newapi）
curl http://localhost/v1/models \
  -H "Authorization: Bearer sk-your-token"
```

---

## 5. 部署 api-logger

### 5.1 确认前置条件

在开始之前，确认以下 Docker 容器都在运行：

```bash
docker ps
```

应该看到：
- `mysql` (端口 33060→3306)
- `nginx` (端口 80)
- `newapi` (端口 55000)
- 各种 vllm 模型容器

### 5.2 确认 Docker 网络

```bash
docker network ls
```

记下你的 Docker 网络名称（如 `newapi-net`）。确保 MySQL、Nginx、newapi 都在同一个网络里：

```bash
docker network inspect newapi-net | grep -E '"Name"|"IPv4Address"'
```

### 5.3 初始化数据库

```bash
# 方式一：从宿主机执行
mysql -h 127.0.0.1 -P 33060 -u root -p < api-logger/schema.sql

# 方式二：从容器内执行
docker exec -i mysql mysql -u root -p<你的密码> < api-logger/schema.sql

# 方式三：进入容器手动执行
docker exec -it mysql mysql -u root -p
# 然后输入密码，再执行：
# source /path/to/schema.sql;
```

验证：

```sql
USE newapi_logs;
SHOW TABLES;
-- 应该看到: api_chat_logs, api_general_logs
```

### 5.4 配置环境变量

```bash
cd newapi-logger

# 复制配置模板
cp .env.example .env

# 编辑配置
vi .env
```

**必须修改的 3 项**：

```bash
# 1. MySQL 密码
MYSQL_PASSWORD=你的实际密码

# 2. newapi 地址（默认即可，如果在同一 Docker 网络）
NEWAPI_URL=http://newapi:55000

# 3. Docker 网络名（改成你的实际网络名）
DOCKER_NETWORK=newapi-net
```

其他保持默认即可：

```bash
# 日志代理端口（避开所有占用端口）
LISTEN_PORT=55020

# HTTP 连接池（500 并发够用）
HTTP_MAX_CONNECTIONS=500

# 数据库写入线程（3 个并行写入）
DB_WORKER_THREADS=3

# 令牌完整保存（用于精确追踪用户）
TOKEN_MASK=false
```

### 5.5 启动

```bash
docker compose up -d
```

### 5.6 查看启动日志

```bash
docker compose logs -f

# 应该看到：
# === newapi-logger v2.0 starting ===
# Connection pool initialized: 2 connections
# Started 3 DB worker threads
# newapi-logger v2.0 ready on 0.0.0.0:55020
```

---

## 6. 验证与测试

### 6.1 健康检查

```bash
curl http://localhost:55020/health
```

正常返回：

```json
{
  "status": "ok",
  "version": "2.0.0",
  "upstream": "http://newapi:55000",
  "db_queue_size": 0,
  "db_queue_max": 10000
}
```

### 6.2 端到端测试

发一个真实的 API 调用：

```bash
# 测试聊天补全
curl http://localhost/v1/chat/completions \
  -H "Authorization: Bearer sk-your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen",
    "messages": [{"role": "user", "content": "你好，请介绍一下自己"}],
    "stream": false
  }'
```

### 6.3 验证日志记录

```sql
-- 查看最近的记录
SELECT
    id,
    token_name,
    model,
    endpoint,
    total_tokens,
    latency_ms,
    created_at
FROM api_chat_logs
ORDER BY id DESC
LIMIT 10;
```

如果看到记录，说明一切正常！

### 6.4 验证宕机回退

```bash
# 停止日志服务
docker compose down

# 再发一次 API 调用 — 应该正常返回（nginx 自动切到 newapi 直连）
curl http://localhost/v1/chat/completions ...

# 恢复日志服务
docker compose up -d
```

---

## 7. 日志查看与数据分析

### 7.1 文件日志

```bash
# 实时查看完整日志
tail -f api-logger/logs/full.log

# 实时查看错误日志
tail -f api-logger/logs/error.log

# 搜索特定用户
grep "sk-xxxx" api-logger/logs/full.log
```

### 7.2 数据库查询

#### 查询某个用户的完整对话

```sql
-- 用令牌完整值精确查找
SELECT
    id,
    model,
    request_body,     -- 用户的问题和历史
    response_body,    -- 模型的回答
    thinking_content, -- 思考过程
    created_at
FROM api_chat_logs
WHERE token_full = 'sk-xxxxxxxxxxxxxxxxxxxxxxxx'
ORDER BY created_at DESC
LIMIT 20;
```

#### 按令牌统计用量（对应 newapi 令牌管理）

```sql
SELECT
    token_name,
    token_full,
    COUNT(*) AS 调用次数,
    SUM(total_tokens) AS 总Token,
    SUM(prompt_tokens) AS 输入Token,
    SUM(completion_tokens) AS 输出Token,
    AVG(latency_ms) AS 平均响应ms,
    MAX(created_at) AS 最后一次调用
FROM api_chat_logs
WHERE is_error = 0
GROUP BY token_name, token_full
ORDER BY 总Token DESC;
```

#### 按模型类型分析

```sql
-- 按端点看不同类型模型的调用分布
SELECT
    CASE
        WHEN endpoint LIKE '%chat/completions%' THEN 'LLM对话'
        WHEN endpoint LIKE '%embeddings%' THEN '词向量'
        WHEN endpoint LIKE '%rerank%' THEN '重排序'
        WHEN endpoint LIKE '%images%' THEN '文生图'
        ELSE '其他'
    END AS 模型类型,
    COUNT(*) AS 调用次数,
    SUM(total_tokens) AS 总Token
FROM api_chat_logs
WHERE is_error = 0
GROUP BY
    CASE
        WHEN endpoint LIKE '%chat/completions%' THEN 'LLM对话'
        WHEN endpoint LIKE '%embeddings%' THEN '词向量'
        WHEN endpoint LIKE '%rerank%' THEN '重排序'
        WHEN endpoint LIKE '%images%' THEN '文生图'
        ELSE '其他'
    END
ORDER BY 调用次数 DESC;
```

#### 分析用户对话内容

```sql
-- 查看包含特定关键词的对话
SELECT
    id,
    token_name,
    model,
    request_body,
    response_body,
    created_at
FROM api_chat_logs
WHERE request_body LIKE '%关键词%'
ORDER BY created_at DESC;
```

#### 查看思考过程（reasoning 模型）

```sql
SELECT
    id,
    model,
    thinking_content,
    LEFT(response_body, 200) AS response_preview,
    created_at
FROM api_chat_logs
WHERE thinking_content IS NOT NULL
ORDER BY created_at DESC
LIMIT 20;
```

#### 性能分析

```sql
-- 慢请求（超过 10 秒）
SELECT
    id,
    endpoint,
    model,
    latency_ms,
    prompt_tokens,
    completion_tokens,
    created_at
FROM api_chat_logs
WHERE latency_ms > 10000
ORDER BY latency_ms DESC
LIMIT 20;

-- 每小时请求量
SELECT
    DATE_FORMAT(created_at, '%Y-%m-%d %H:00') AS hour,
    COUNT(*) AS requests,
    AVG(latency_ms) AS avg_latency,
    SUM(total_tokens) AS total_tokens
FROM api_chat_logs
GROUP BY hour
ORDER BY hour DESC
LIMIT 48;
```

---

## 8. 日常运维

### 启停命令

```bash
cd newapi-logger

docker compose up -d       # 启动日志服务
docker compose down        # 停止日志服务
docker compose restart     # 重启
docker compose logs -f     # 查看实时日志
docker compose ps          # 查看状态
```

### 空间管理

```bash
# 查看日志占用
du -sh api-logger/logs/

# 清理 30 天前的日志文件
find api-logger/logs/ -name "*.log.*" -mtime +30 -delete
```

### MySQL 数据清理

```sql
-- 保留最近 90 天
DELETE FROM api_chat_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
DELETE FROM api_general_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- 可选：设置定时任务
CREATE EVENT IF NOT EXISTS clean_old_logs
ON SCHEDULE EVERY 1 DAY
DO
  DELETE FROM api_chat_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
```

### 修改配置

```bash
# 1. 修改 .env
vi .env

# 2. 重启生效
docker compose restart
```

---

## 9. 故障排查

### 日志服务启动失败

```bash
# 查看详细错误
docker compose logs

# 常见原因：
# 1. MySQL 连不上 → 检查 MYSQL_PASSWORD 和 Docker 网络
# 2. 端口冲突 → 检查 LISTEN_PORT
```

### MySQL 连接失败

```bash
# 测试连通性
docker exec api-logger ping mysql

# 如果 ping 不通，检查是否在同一 Docker 网络
docker network inspect newapi-net | grep -E "api-logger|mysql"

# 手动测试 MySQL 连接
docker exec api-logger python3 -c "
import pymysql
pymysql.connect(host='mysql', port=3306, user='root', password='你的密码')
print('OK')
"
```

### Nginx 报 502

```bash
# logger 与 newapi 之间连接问题
docker exec api-logger curl -s http://newapi:55000/
# 看 newapi 是否正常响应
```

### 队列满载

健康检查中 `db_queue_size` 持续接近 10000，说明 MySQL 写入跟不上。

**解决方法**：

```bash
# 在 .env 中增加 worker 线程
DB_WORKER_THREADS=5

# 或者增加连接池
DB_POOL_MAX_SIZE=12

# 重启
docker compose restart
```

### 流式响应中断

确保 Nginx 配置中有：

```nginx
proxy_buffering off;
```

---

## 10. 离线环境导入

### 你需要的东西

拷贝到 U 盘：
- 整个 `newapi-logger/` 文件夹（~120MB）
- `api-logger.tar`（Docker 镜像，59MB）
- `python-3.11-slim.tar`（基础镜像，46MB）

### 离线服务器操作

```bash
# 1. 导入 Docker 镜像
docker load -i python-3.11-slim.tar
docker load -i api-logger.tar

# 2. 确认镜像
docker images | grep -E "api-logger|python.*3.11"

# 3. 修改配置
cd newapi-logger
cp .env.example .env
vi .env   # 改 MYSQL_PASSWORD 和 DOCKER_NETWORK

# 4. 启动
docker compose up -d

# 5. 验证
curl http://localhost:55020/health
```

### 如果离线环境没有 MySQL

MySQL 镜像也可以离线导入：

```bash
# 在线环境导出
docker pull mysql:8.0
docker save -o mysql-8.0.tar mysql:8.0

# 离线导入
docker load -i mysql-8.0.tar
```

---

## 文件清单

```
newapi-logger/
├── README.md                         ← 本文件（从这里开始看）
├── offline-checklist.md              ← 一页纸速查卡
├── docker-compose.yml                ← 启停用这个
├── .env.example                      ← 配置文件模板
├── api-logger.tar          (59MB)    ← 预构建 Docker 镜像
├── python-3.11-slim.tar    (46MB)    ← Python 基础镜像
├── api-logger/
│   ├── main.py                       ← 透明代理主程序
│   ├── config.py                     ← 配置文件
│   ├── database.py                   ← 数据库操作（连接池+多线程）
│   ├── schema.sql                    ← MySQL 建表脚本
│   ├── requirements.txt              ← Python 依赖
│   ├── Dockerfile                    ← 在线构建
│   ├── Dockerfile.offline            ← 离线构建
│   ├── wheels-linux/   (23个包)      ← Linux 离线 pip 包
│   └── logs/                         ← 日志文件（运行时）
└── nginx/
    └── nginx-example.conf            ← Nginx 完整配置参考
```
