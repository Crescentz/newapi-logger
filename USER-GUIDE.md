# newapi-logger 使用手册 v2.0

> **适用人群**：对 Docker/Linux/MySQL 零基础的用户，每一步都有解释。
> **目标**：拷贝到服务器后，30 分钟内跑起来。

---

## 0. 快速开始（老手看这里）

```bash
# 在线环境
cd newapi-logger
cp deploy/.env.example deploy/.env
vi deploy/.env          # 改 MYSQL_PASSWORD、DOCKER_NETWORK、NEWAPI_DATA_DIR
docker compose -f deploy/docker-compose.yml up -d
curl http://localhost:55020/health

# 离线环境 → 见第 3 节
```

---

## 1. 这个项目是干什么的？

你的环境大概长这样：

```
用户/应用 → Nginx(55010) → newapi(55000) → 各种大模型(55001-55003)
                                          → bge词向量(55006)
                                          → 通义多模态(54001)
```

**问题**：newapi 自带的日志只能看到调用次数，看不到"用户问了什么、模型回了什么"。

**解决**：在 Nginx 和 newapi 之间插一个"透明摄像头"（api-logger），路过什么就记录什么，但不影响原来的服务：

```
用户/应用 → Nginx(55010) → api-logger(55020) → newapi(55000) → 模型们
                              │
                              ├─ 写文件日志 (logs/full.log)
                              └─ 写 MySQL 数据库 (永久存储)
```

**三个关键设计**：
- **不改原服务**：newapi 和 vllm 一行代码都不动
- **宕机不碍事**：api-logger 挂了，Nginx 自动绕过去直连 newapi
- **令牌追踪**：能精确知道"这个请求是哪个用户（令牌）发的"

---

## 2. 你需要准备什么？

### 必需环境
| 项目 | 说明 |
|------|------|
| Linux 服务器 | 64位，建议 Ubuntu 20.04+ / CentOS 7+ |
| Docker | 已安装 `docker` 和 `docker compose` |
| MySQL | 可以用已有的，也可以新建（见下方） |
| 网络 | Docker 网络，确保各服务在同一网络内 |

### 端口清单（部署前检查不冲突）

| 端口 | 用途 | 是否可改 |
|------|------|---------|
| 55020 | api-logger 监听端口 | ✅ 可改 |
| 55000 | newapi 端口 | ✅ 可改 |
| 3306 | MySQL 容器内端口 | ✅ 可改 |
| 33060 | MySQL 宿主机端口 | ✅ 可改 |

> 📝 **修改端口的方法**：编辑 `.env` 文件中对应的变量，然后 `docker compose restart`。

### 挂载路径清单（部署前确认有足够空间）

| 路径 | 用途 | 是否可改 |
|------|------|---------|
| `LOG_HOST_DIR` (默认 `./logs`) | 日志文件存放 | ✅ 可改 |
| `NEWAPI_DATA_DIR` (默认 `/opt/newapi/data`) | newapi 的 SQLite 数据库 | ✅ 可改 |

> 📝 **修改挂载路径的方法**：编辑 `.env` 文件中对应的变量，确保目录已创建，然后重启。

---

## 3. 离线环境部署（从零开始，逐行复制）

> 适用场景：服务器不能上网，你只能靠 U 盘拷贝文件。

### 步骤 0：确认你需要拷贝哪些文件

把整个 `newapi-logger/offline-package/` 目录拷贝到 U 盘。目录结构如下：

```
offline-package/
├── api-logger-v2.2.tar        ← Docker 镜像（约 59MB）
├── python-3.11-slim.tar        ← Python 基础镜像（约 46MB）
├── Dockerfile.offline          ← 离线构建文件（备用）
├── docker-compose.yml          ← 一键启动/停止
├── .env.example                ← 配置模板
├── schema.sql                  ← 建数据库表
├── nginx-example.conf          ← Nginx 配置参考
├── README.md                   ← 完整文档
├── offline-checklist.md        ← 一页纸速查卡
├── requirements.txt            ← Python 依赖清单
├── wheels/                     ← 离线 Python 包（23个）
│   ├── fastapi-*.whl
│   ├── httpx-*.whl
│   ├── pydantic-*.whl
│   └── ... (共 23 个)
└── src/
    ├── main.py                 ← 透明代理主程序
    ├── config.py               ← 配置文件
    ├── database.py             ← 数据库操作
    └── token_resolver.py       ← 令牌名称解析
```

### 步骤 1：导入 Docker 镜像

把 U 盘插到离线服务器上，假设 U 盘挂载在 `/mnt/usb`：

```bash
# 导入 Python 基础镜像（只需要做一次）
docker load -i /mnt/usb/offline-package/python-3.11-slim.tar

# 导入 api-logger 镜像（只需要做一次）
docker load -i /mnt/usb/offline-package/api-logger-v2.2.tar

# 验证镜像导入成功
docker images | grep -E "api-logger|python.*3.11"
# 应该看到两个镜像
```

> 💡 **解释**：`.tar` 文件是 Docker 镜像的"安装包"，`docker load` 就是安装它。

### 步骤 2：初始化 MySQL 数据库

> 如果你已经有一个 MySQL 在运行，且创建了 `newapi_logs` 数据库，跳到步骤 3。

```bash
# 方式一：从服务器直接操作 MySQL（如果你装了 mysql 客户端）
mysql -h 127.0.0.1 -P 33060 -u root -p < /mnt/usb/offline-package/schema.sql

# 方式二：从 Docker 容器内操作（推荐，不依赖宿主机装 MySQL 客户端）
docker exec -i mysql mysql -u root -p你的密码 < /mnt/usb/offline-package/schema.sql

# 方式三：手动执行（如果上面的命令报错）
docker exec -it mysql mysql -u root -p
# 输入密码后，逐行执行 schema.sql 里的 SQL 语句
```

验证数据库创建成功：

```sql
-- 进入 MySQL 后执行
USE newapi_logs;
SHOW TABLES;
-- 应该输出: api_chat_logs, api_general_logs （两张表）
```

### 步骤 3：配置环境变量

```bash
# 把 offline-package 整个目录拷贝到服务器上你喜欢的位置
cp -r /mnt/usb/offline-package /opt/newapi-logger
cd /opt/newapi-logger

# 创建配置文件
cp .env.example .env

# 编辑配置（用 vi、nano 或你熟悉的编辑器）
vi .env
```

**你至少需要修改 3 个值**：

```bash
# ① MySQL 密码（必改！改成你实际设置的密码）
MYSQL_PASSWORD=你的实际密码

# ② Docker 网络名（改成你实际的网络名，默认通常是 newapi-net）
DOCKER_NETWORK=newapi-net

# ③ newapi 的 data 目录路径（把 /opt/newapi/data 换成你实际的路径）
NEWAPI_DATA_DIR=/opt/newapi/data
```

**如果你想改端口**（可选）：

```bash
# 例如把代理端口从 55020 改成 18080
LISTEN_PORT=18080

# ⚠️ 改了端口后，还需要去 Nginx 配置里同步修改 upstream
# 把 server api-logger:55020 改成 server api-logger:18080
```

**如果你想改日志存放位置**（可选）：

```bash
# 例如把日志存到 /data/logs/（确保目录已创建 mkdir -p /data/logs）
LOG_HOST_DIR=/data/logs
```

### 步骤 4：修改 Nginx 配置

> ⚠️ **重要**：api-logger 是一个透明代理，它需要插在 Nginx 和 newapi 之间。你需要修改 Nginx 的 upstream 配置。

找到你的 Nginx 配置文件（通常在 `/data/nginx/conf.d/newapi.conf`），把 upstream 部分改成：

```nginx
upstream newapi_backend {
    # api-logger 作为主节点（流量走这里就会记录日志）
    server api-logger:55020 max_fails=3 fail_timeout=30s;

    # newapi 直连作为备用（api-logger 挂了自动切换，不影响使用）
    server newapi:55000 backup;

    keepalive 128;
    keepalive_timeout 60s;
    keepalive_requests 2000;
}
```

> 💡 **关键点**：`backup` 关键字让 Nginx 只在 api-logger 不可用时才走 newapi 直连。

然后重启 Nginx 让配置生效：

```bash
docker restart nginx

# 验证配置没出错
docker exec nginx nginx -t
```

### 步骤 5：启动 api-logger

```bash
cd /opt/newapi-logger

# 启动（-d 表示后台运行）
docker compose up -d

# 查看启动日志
docker compose logs -f
```

看到以下输出说明启动成功：

```
2026-06-01 15:30:00 | INFO    | === newapi-logger v2.0 starting ===
2026-06-01 15:30:01 | INFO    | Connection pool initialized: 2 connections
2026-06-01 15:30:01 | INFO    | Started 3 DB worker threads
2026-06-01 15:30:01 | INFO    | newapi-logger v2.0 ready on 0.0.0.0:55020
```

### 步骤 6：验证服务正常

```bash
# 健康检查
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

然后发一个真实的 API 请求测试（通过 Nginx 入口）：

```bash
curl http://localhost:55010/v1/chat/completions \
  -H "Authorization: Bearer sk-你的令牌" \
  -H "Content-Type: application/json" \
  -d '{"model":"你的模型名","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

应该正常返回模型的回复，同时日志被记录了。

### 步骤 7：验证日志已写入

```bash
# 方式一：看文件日志
tail -20 /opt/newapi-logger/logs/full.log

# 方式二：查 MySQL 数据库
docker exec -it mysql mysql -u root -p -e "
  SELECT id, token_name, model, total_tokens, created_at
  FROM newapi_logs.api_chat_logs
  ORDER BY id DESC LIMIT 5;
"
```

---

## 4. 日常操作命令

```bash
cd /opt/newapi-logger    # 先进入项目目录

# === 启动/停止 ===
docker compose up -d        # 启动（后台运行）
docker compose down         # 停止（不影响 Nginx/newapi）
docker compose restart      # 重启
docker compose ps           # 查看运行状态
docker compose logs -f      # 实时看日志（Ctrl+C 退出）

# === 修改配置 ===
vi .env                     # 编辑配置
docker compose restart      # 重启让配置生效

# === 查看日志 ===
tail -f logs/full.log       # 实时看完整日志
tail -f logs/error.log      # 实时看错误日志
du -sh logs/                # 看日志占了多少空间
```

---

## 5. 常用数据库查询

> 💡 用 `docker exec -it mysql mysql -u root -p` 进入 MySQL 后执行。

### 看最近谁在用什么模型

```sql
SELECT token_name, model, COUNT(*) as 次数, SUM(total_tokens) as 总Token
FROM newapi_logs.api_chat_logs
WHERE is_error = 0
GROUP BY token_name, model
ORDER BY 总Token DESC
LIMIT 20;
```

### 查某个用户的完整对话记录

```sql
-- 把 sk-xxx 换成实际的令牌值
SELECT id, model, request_body, response_body, created_at
FROM newapi_logs.api_chat_logs
WHERE token_full = 'sk-你的令牌值'
ORDER BY created_at DESC
LIMIT 20;
```

### 看哪些请求比较慢（超过 10 秒）

```sql
SELECT endpoint, model, latency_ms, created_at
FROM newapi_logs.api_chat_logs
WHERE latency_ms > 10000
ORDER BY latency_ms DESC
LIMIT 20;
```

### 统计每小时调用量

```sql
SELECT DATE_FORMAT(created_at, '%Y-%m-%d %H:00') as 小时,
       COUNT(*) as 请求数, AVG(latency_ms) as 平均延迟ms
FROM newapi_logs.api_chat_logs
GROUP BY 小时
ORDER BY 小时 DESC
LIMIT 48;
```

---

## 6. 故障排查

### 问题 1：启动失败，看日志提示 "Can't connect to MySQL"

```bash
# 检查 MySQL 是否在同一个 Docker 网络
docker network inspect newapi-net | grep -E "api-logger|mysql"

# 测试 api-logger 容器能否 ping 通 MySQL
docker exec api-logger ping mysql

# 如果 ping 不通，检查 docker-compose.yml 中的 DOCKER_NETWORK 配置
```

### 问题 2：健康检查返回错误

```bash
# 看 api-logger 日志
docker compose logs --tail 50

# 常见原因：.env 中 MYSQL_PASSWORD 写错了
```

### 问题 3：Nginx 返回 502 Bad Gateway

```bash
# api-logger 可能没有正常转发到 newapi
# 测试 api-logger 能否访问 newapi
docker exec api-logger python3 -c "
import httpx
r = httpx.get('http://newapi:55000/')
print(r.status_code)
"
```

### 问题 4：日志队列积压（/health 返回 db_queue_size 很大）

```bash
# 说明 MySQL 写入速度跟不上请求速度
# 解决方法：在 .env 中增加写入线程
DB_WORKER_THREADS=5
DB_POOL_MAX_SIZE=12

# 然后重启
docker compose restart
```

### 问题 5：流式响应卡住不动

确认 Nginx 配置中有这两行（通常在 `proxy_buffering` 附近）：

```nginx
proxy_buffering off;
proxy_request_buffering off;
```

---

## 7. 验证宕机保护（确认不影响原服务）

```bash
# 1. 停掉 api-logger
docker compose down

# 2. 发一个 API 请求 — 应该正常返回（Nginx 自动绕过 api-logger 直连 newapi）
curl http://localhost:55010/v1/chat/completions ...

# 3. 恢复 api-logger
docker compose up -d
```

---

## 8. 配置参数完整参考

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `NEWAPI_URL` | `http://newapi:55000` | newapi 地址 |
| `LISTEN_HOST` | `0.0.0.0` | 监听地址 |
| `LISTEN_PORT` | `55020` | 监听端口 |
| `MYSQL_HOST` | `mysql` | MySQL 主机名 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_USER` | `root` | MySQL 用户名 |
| `MYSQL_PASSWORD` | *（无）* | MySQL 密码（必填） |
| `MYSQL_DATABASE` | `newapi_logs` | 数据库名 |
| `NEWAPI_SQLITE_PATH` | `/newapi-data/one-api.db` | newapi SQLite 路径（容器内） |
| `NEWAPI_DATA_DIR` | `/opt/newapi/data` | 宿主机 newapi data 目录 |
| `TOKEN_NAME_CACHE_TTL` | `300` | 令牌名称缓存秒数（0=禁用） |
| `DB_WORKER_THREADS` | `3` | 数据库写入线程数 |
| `DB_POOL_MIN_SIZE` | `2` | 连接池最小连接数 |
| `DB_POOL_MAX_SIZE` | `8` | 连接池最大连接数 |
| `DB_QUEUE_MAXSIZE` | `10000` | 写入队列最大长度 |
| `HTTP_MAX_CONNECTIONS` | `500` | HTTP 连接池大小 |
| `LOG_LEVEL` | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `LOG_HOST_DIR` | `./logs` | 日志文件存放目录 |
| `TOKEN_MASK` | `false` | 是否脱敏令牌（false=完整保存） |
| `DOCKER_NETWORK` | `newapi-net` | Docker 网络名 |

---

## 9. 清理与维护

### 清理旧日志（防止磁盘撑爆）

```bash
# 查看日志占用
du -sh logs/

# 删除 30 天前的日志文件
find logs/ -name "*.log.*" -mtime +30 -delete
```

### 清理 MySQL 旧数据

```sql
-- 删除 90 天前的记录
DELETE FROM newapi_logs.api_chat_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
DELETE FROM newapi_logs.api_general_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- 设置自动清理（每天凌晨执行）
CREATE EVENT IF NOT EXISTS clean_old_logs
ON SCHEDULE EVERY 1 DAY
DO
  DELETE FROM newapi_logs.api_chat_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
```

---

## 10. 附录：项目文件清单

```
newapi-logger/
├── USER-GUIDE.md                  ← 👈 你正在看这个（小白从这里开始）
├── README.md                      ← 完整技术文档
├── .gitignore
│
├── src/                           ← 源代码（单一真相源）
│   ├── main.py                    ← 透明代理主程序
│   ├── config.py                  ← 配置读取
│   ├── database.py                ← 数据库连接池+写入
│   ├── token_resolver.py          ← 令牌名称解析
│   └── requirements.txt           ← Python 依赖
│
├── deploy/                        ← 在线部署文件
│   ├── docker-compose.yml         ← Docker Compose 编排
│   ├── Dockerfile                 ← 在线构建
│   ├── Dockerfile.offline         ← 离线构建
│   ├── .env.example               ← 配置模板
│   ├── schema.sql                 ← 建表 SQL
│   └── nginx-example.conf         ← Nginx 配置参考
│
├── offline-package/               ← 离线部署包（拷到 U 盘用这个）
│   ├── docker-compose.yml         ← 一键启动
│   ├── Dockerfile.offline         ← 离线重建（备用）
│   ├── .env.example               ← 配置模板
│   ├── schema.sql                 ← 建表 SQL
│   ├── nginx-example.conf         ← Nginx 参考
│   ├── README.md                  ← 文档
│   ├── offline-checklist.md       ← 速查卡
│   ├── requirements.txt           ← 依赖清单
│   ├── api-logger-v2.2.tar        ← Docker 镜像
│   ├── python-3.11-slim.tar       ← Python 基础镜像
│   ├── wheels/                    ← 离线 Python 包
│   └── src/                       ← 源代码（和 src/ 同步）
│
└── docs/
    ├── README.zh.md               ← 项目简介
    └── offline-checklist.md       ← 离线速查卡
```
