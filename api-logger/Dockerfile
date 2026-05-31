# ============================================================
# newapi-logger Dockerfile（在线构建版）
# 需要网络访问 PyPI（或使用清华镜像）
#
# 构建: docker build -t api-logger:latest .
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制源代码
COPY *.py /app/

# 创建日志目录
RUN mkdir -p /app/logs

# 暴露端口（与 .env 中 LISTEN_PORT 一致）
EXPOSE 8100

# 启动：单 worker 高并发 async 模式
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100", "--log-level", "info", "--limit-concurrency", "1000"]
