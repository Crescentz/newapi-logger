"""
newapi-logger 配置文件
所有配置通过环境变量传入，方便 Docker 部署和离线环境调整

端口参考（根据实际环境修改）：
  newapi 对外端口      : 55000
  vllm 大模型          : 55001 / 55002 / 55003
  bge embedding        : 55006
  rerank               : 55006
  通义-Z-imate-turbo   : 54001
"""
import os

# ========== newapi 后端地址 ==========
NEWAPI_URL = os.getenv("NEWAPI_URL", "http://newapi:55000")
NEWAPI_HOST = os.getenv("NEWAPI_HOST", "newapi")
NEWAPI_PORT = os.getenv("NEWAPI_PORT", "55000")

# ========== 本服务监听端口 ==========
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8100"))
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")

# ========== MySQL 连接配置 ==========
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "newapi_logs")

# ========== MySQL 连接池配置（高并发优化）==========
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "8"))
DB_WORKER_THREADS = int(os.getenv("DB_WORKER_THREADS", "3"))
DB_QUEUE_MAXSIZE = int(os.getenv("DB_QUEUE_MAXSIZE", "10000"))

# ========== 日志配置 ==========
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 完整日志文件配置
FULL_LOG_MAX_MB = int(os.getenv("FULL_LOG_MAX_MB", "100"))
FULL_LOG_BACKUPS = int(os.getenv("FULL_LOG_BACKUPS", "30"))

# 错误日志文件配置
ERROR_LOG_MAX_MB = int(os.getenv("ERROR_LOG_MAX_MB", "50"))
ERROR_LOG_BACKUPS = int(os.getenv("ERROR_LOG_BACKUPS", "10"))

# ========== 详细记录端点 ==========
# 聊天补全（大语言模型、多模态理解、tool calling 等）
DETAILED_CHAT_ENDPOINTS = [
    "/v1/chat/completions",
    "/v1/completions",
]

# embedding 端点（bge 等词向量模型）
DETAILED_EMBEDDING_ENDPOINTS = [
    "/v1/embeddings",
]

# rerank 端点（重排序模型）
DETAILED_RERANK_ENDPOINTS = [
    "/v1/rerank",
]

# 图像生成端点（文生图）
DETAILED_IMAGE_ENDPOINTS = [
    "/v1/images/generations",
]

# ========== 请求体最大记录大小 ==========
MAX_BODY_LOG_SIZE = int(os.getenv("MAX_BODY_LOG_SIZE", "5242880"))  # 5MB
# 流式响应最大累积大小（防止内存溢出）
MAX_STREAM_BUFFER_SIZE = int(os.getenv("MAX_STREAM_BUFFER_SIZE", "10485760"))  # 10MB

# ========== HTTP 连接池（高并发优化）==========
HTTP_MAX_CONNECTIONS = int(os.getenv("HTTP_MAX_CONNECTIONS", "500"))
HTTP_MAX_KEEPALIVE = int(os.getenv("HTTP_MAX_KEEPALIVE", "100"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "300"))
HTTP_CONNECT_TIMEOUT = int(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))

# ========== 代理超时配置 ==========
PROXY_READ_TIMEOUT = int(os.getenv("PROXY_READ_TIMEOUT", "600"))  # 流式长连接

# ========== 令牌脱敏 ==========
# True: token存库时只保留前后8位脱敏; False: 完整保存
TOKEN_MASK = os.getenv("TOKEN_MASK", "false").lower() == "true"


def get_summary() -> str:
    """打印当前配置摘要"""
    return f"""
    NEWAPI_URL       = {NEWAPI_URL}
    LISTEN           = {LISTEN_HOST}:{LISTEN_PORT}
    MYSQL            = {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}
    DB_POOL          = {DB_POOL_MIN_SIZE}-{DB_POOL_MAX_SIZE} connections, {DB_WORKER_THREADS} workers
    HTTP_POOL        = {HTTP_MAX_CONNECTIONS} connections max
    LOG_DIR          = {LOG_DIR}
    LOG_LEVEL        = {LOG_LEVEL}
    TOKEN_MASK       = {TOKEN_MASK}
    """.strip()
