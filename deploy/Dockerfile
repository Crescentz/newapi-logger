# ============================================================
# newapi-logger Dockerfile（在线构建版）
# 构建上下文 = 项目根目录
# 构建: docker build -t api-logger:latest -f deploy/Dockerfile .
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制源代码
COPY src/*.py /app/

# 创建日志目录
RUN mkdir -p /app/logs

EXPOSE 8100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100", "--log-level", "info", "--limit-concurrency", "1000"]
