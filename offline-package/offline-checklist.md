# newapi-logger 离线部署完整性清单 v2.1

> 拷贝到离线服务器前，逐项核对。打勾 = 已备好，空框 = 需要补充。

---

## 📦 已有物料（offline-package/ 目录内）

| # | 物品 | 路径 | 大小 | 状态 |
|---|------|------|------|------|
| 1 | api-logger Docker 镜像 | `api-logger-v2.2.tar` | 59MB | ✅ |
| 2 | Python 3.11 基础镜像 | `python-3.11-slim.tar` | 46MB | ✅ |
| 3 | docker-compose 编排文件 | `docker-compose.yml` | 4KB | ✅ |
| 4 | 环境变量模板 | `.env.example` | 2KB | ✅ |
| 5 | MySQL 建表脚本 | `schema.sql` | 4KB | ✅ |
| 6 | Nginx 配置参考 | `nginx-example.conf` | 2KB | ✅ |
| 7 | Python 离线包 | `wheels/` (23个) | ~21MB | ✅ |
| 8 | 离线 Dockerfile | `Dockerfile.offline` | 1KB | ✅ |
| 9 | 源代码 | `src/` (4个.py) | 36KB | ✅ |
| 10 | requirements.txt | `requirements.txt` | 104B | ✅ |
| 11 | 速查卡 | `offline-checklist.md` | 2KB | ✅ |
| 12 | 完整文档 | `README.md` | 19KB | ✅ |

---

## 🔧 离线服务器需要预装/预置的

| # | 物品 | 如何准备 | 状态 |
|---|------|---------|------|
| 1 | Docker 引擎 | 服务器已安装 `docker` + `docker compose` | ☐ |
| 2 | MySQL 8.0 | 已运行或需拉取 `mysql:8.0` 镜像 | ☐ |
| 3 | Nginx | 已运行或需拉取 `nginx:latest` 镜像 | ☐ |
| 4 | Docker 网络 `newapi-net` | `docker network create newapi-net` | ☐ |
| 5 | newapi 已运行 | 确认 `docker ps` 能看到 newapi 容器 | ☐ |

### 如果离线服务器没有 MySQL/Nginx Docker 镜像

在线环境预先导出（额外操作）：

```bash
# 在线环境执行
docker pull mysql:8.0
docker pull nginx:latest
docker save -o mysql-8.0.tar mysql:8.0
docker save -o nginx-latest.tar nginx:latest
# 把这两个 .tar 文件也拷贝到 U 盘

# 离线服务器导入
docker load -i mysql-8.0.tar
docker load -i nginx-latest.tar
```

---

## ⚠️ 重要注意事项

### 关于预构建镜像

`api-logger-v2.2.tar` 是已构建好的镜像，可以**直接使用**，无需 rebuild。

但注意：
- **修改 LISTEN_PORT 需要重建镜像**（当前镜像 CMD 硬编码 55020）
- 不想重建？保持 `LISTEN_PORT=55020` 即可直接使用

### 关于 wheels 目录

`wheels/` 是**重建镜像时**所需的离线 Python 包，直接使用预构建镜像时不需要。

重建命令：
```bash
cd offline-package
docker build -t api-logger:latest -f Dockerfile.offline .
```

### 重建 vs 直接使用

| 场景 | 方案 |
|------|------|
| 不改端口，直接用 | `docker load -i api-logger-v2.2.tar` → `docker compose up -d` |
| 需要改端口 | 重建镜像: `docker build -f Dockerfile.offline .` → `docker compose up -d` |
| 需要改代码 | 修改 `src/*.py` → 重建镜像 |

---

## 🚀 6 步部署（离线环境）

```bash
# 1. 导入镜像
docker load -i python-3.11-slim.tar
docker load -i api-logger-v2.2.tar

# 2. 确认镜像
docker images | grep api-logger

# 3. 初始化数据库
docker exec -i mysql mysql -u root -p你的密码 < schema.sql

# 4. 配置
cp .env.example .env
vi .env   # 改 MYSQL_PASSWORD, DOCKER_NETWORK, NEWAPI_DATA_DIR

# 5. 修改 Nginx upstream
# 在 /data/nginx/conf.d/newapi.conf 的 upstream 中添加:
#   server api-logger:55020 max_fails=3 fail_timeout=30s;
#   server newapi:55000 backup;
docker restart nginx

# 6. 启动 + 验证
docker compose up -d
curl http://localhost:55020/health
# → {"status":"ok","version":"2.0.0"}
```
