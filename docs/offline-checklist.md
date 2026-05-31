# 离线环境部署速查卡 v2.0

> 6 步搞定。详细说明见 README.md。

---

## 端口速查

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx (对外) | 80 | API 入口 |
| api-logger | 8100 | 日志代理（内部） |
| newapi | 55000 | API 管理 |
| vllm 大模型 | 55001-55003 | 多个模型 |
| bge/rerank | 55006 | 词向量+重排序 |
| 通义多模态 | 54001 | 图像理解 |
| MySQL | 33060→3306 | 日志存储 |

---

## 离线部署 6 步

### 1. 导入镜像
```bash
docker load -i python-3.11-slim.tar
docker load -i api-logger.tar
```

### 2. 初始化数据库
```bash
mysql -h 127.0.0.1 -P 33060 -u root -p < api-logger/schema.sql
```

### 3. 配置
```bash
cd newapi-logger && cp .env.example .env && vi .env
# 改 MYSQL_PASSWORD 和 DOCKER_NETWORK
```

### 4. Nginx upstream
```nginx
upstream newapi_backend {
    server api-logger:8100 max_fails=3 fail_timeout=30s;
    server newapi:55000 backup;
    keepalive 128;
}
```
```bash
docker restart nginx
```

### 5. 启动
```bash
docker compose up -d
```

### 6. 验证
```bash
curl http://localhost:8100/health
# → {"status":"ok","version":"2.0.0"}
```

---

## 常用命令

```bash
docker compose up -d        # 启动
docker compose down         # 停止（不影响原服务）
docker compose restart      # 重启
docker compose logs -f      # 日志
curl localhost:8100/health  # 健康

tail -f api-logger/logs/full.log    # 完整日志
tail -f api-logger/logs/error.log   # 错误日志
```

## 查数据

```sql
-- 按用户查对话
SELECT * FROM api_chat_logs WHERE token_full='sk-xxx' ORDER BY id DESC LIMIT 20;

-- 按令牌统计（对应 newapi 令牌管理）
SELECT token_name, COUNT(*), SUM(total_tokens) FROM api_chat_logs GROUP BY token_name, token_full;

-- 按模型类型分析
SELECT endpoint, COUNT(*), SUM(total_tokens) FROM api_chat_logs GROUP BY endpoint;
```
