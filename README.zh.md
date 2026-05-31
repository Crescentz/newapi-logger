# newapi-logger

在 newapi 前面插一层透明代理，记录所有 API 调用的完整内容（LLM 对话、词向量、重排序、多模态理解、文生图）。

## 特性

- **全模型日志**：LLM / embedding / rerank / 图像生成，全端点详细记录
- **用户追踪**：完整保存令牌（token_full），精确对应 newapi 的令牌管理
- **高并发**：500 QPS+，httpx 连接池 + DB 连接池 + 多线程写入
- **零侵入**：不改 newapi/vllm 一行代码，独立启停
- **宕机保护**：Nginx backup 机制，日志服务宕机时自动直连原服务

## 快速开始

见 [README.md](README.md)

## 目录

```
newapi-logger/
├── README.md                    # 完整操作手册（小白也能看懂）
├── offline-checklist.md         # 一页纸速查卡
├── docker-compose.yml           # 一键启动
├── .env.example                 # 配置文件模板
├── api-logger/
│   ├── main.py                  # 透明代理主程序 (v2.0)
│   ├── config.py                # 配置文件
│   ├── database.py              # 数据库操作 (连接池 + 多线程)
│   ├── schema.sql               # MySQL 建表脚本
│   ├── requirements.txt         # Python 依赖
│   ├── Dockerfile               # Docker 镜像 (在线构建)
│   └── Dockerfile.offline       # Docker 镜像 (离线构建)
├── nginx/
│   └── nginx-example.conf       # Nginx 完整配置参考
└── docs/
    └── (暂无，见 README.md)
```

## 支持的端点

| 端点 | 记录级别 | 说明 |
|------|---------|------|
| `/v1/chat/completions` | 详细 | LLM 对话、多模态、tool calling |
| `/v1/completions` | 详细 | 文本补全 |
| `/v1/embeddings` | 详细 | 词向量（bge 等） |
| `/v1/rerank` | 详细 | 重排序 |
| `/v1/images/generations` | 详细 | 文生图 |
| 其他所有端点 | 简要 | 透明转发 + 基础记录 |
