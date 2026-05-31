"""
newapi-logger — 透明日志代理 v2.0

部署在 nginx 和 newapi 之间，透明转发所有请求，异步记录完整调用详情。

支持端点：
  - /v1/chat/completions   → 大语言模型、多模态理解、tool calling（详细记录）
  - /v1/completions         → 文本补全（详细记录）
  - /v1/embeddings          → 词向量 / bge（详细记录）
  - /v1/rerank              → 重排序（详细记录）
  - /v1/images/generations  → 文生图（详细记录）
  - 其他所有端点            → 透明转发 + 简要记录

高并发设计（500 QPS+）：
  - httpx 连接池 500，长连接复用
  - 3 个 DB worker 线程并行写入
  - 队列满时降级写文件，不丢日志
  - 流式响应内存截断保护（10MB上限）
"""
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response as FastAPIResponse
from fastapi.responses import StreamingResponse, PlainTextResponse

import config
import database
import token_resolver

# ============================
#  日志系统初始化
# ============================

os.makedirs(config.LOG_DIR, exist_ok=True)

_full_log = logging.getLogger("full")
_full_log.setLevel(logging.DEBUG)
_full_log.propagate = False
_fh = RotatingFileHandler(
    os.path.join(config.LOG_DIR, "full.log"),
    maxBytes=config.FULL_LOG_MAX_MB * 1024 * 1024,
    backupCount=config.FULL_LOG_BACKUPS,
    encoding="utf-8",
)
_fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d | %(message)s", "%Y-%m-%d %H:%M:%S"))
_full_log.addHandler(_fh)

_error_log = logging.getLogger("error")
_error_log.setLevel(logging.WARNING)
_error_log.propagate = False
_eh = RotatingFileHandler(
    os.path.join(config.LOG_DIR, "error.log"),
    maxBytes=config.ERROR_LOG_MAX_MB * 1024 * 1024,
    backupCount=config.ERROR_LOG_BACKUPS,
    encoding="utf-8",
)
_eh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
_error_log.addHandler(_eh)

_console = logging.getLogger("console")
_console.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.DEBUG))
_console.propagate = False
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
_console.addHandler(_ch)

_console.info("=== newapi-logger v2.0 starting ===")
_console.info(config.get_summary())

# ============================
#  HTTP 客户端（高并发连接池）
# ============================

_http_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=config.HTTP_TIMEOUT,
                connect=config.HTTP_CONNECT_TIMEOUT,
                read=config.PROXY_READ_TIMEOUT,
            ),
            limits=httpx.Limits(
                max_connections=config.HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=config.HTTP_MAX_KEEPALIVE,
                keepalive_expiry=30,
            ),
        )
    return _http_client


# ============================
#  FastAPI 应用
# ============================

app = FastAPI(title="newapi-logger", version="2.0.0", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def startup():
    database.start_db_workers()
    token_resolver.init()
    _console.info("newapi-logger v2.0 ready on %s:%d", config.LISTEN_HOST, config.LISTEN_PORT)


@app.on_event("shutdown")
async def shutdown_event():
    database.shutdown()
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    _console.info("newapi-logger shut down")


# ============================
#  端点分类
# ============================

def _classify_endpoint(path: str) -> str:
    """将端点分类：chat / embedding / rerank / image / general"""
    p = path.rstrip("/")
    for ep in config.DETAILED_CHAT_ENDPOINTS:
        if p.endswith(ep.rstrip("/")):
            return "chat"
    for ep in config.DETAILED_EMBEDDING_ENDPOINTS:
        if p.endswith(ep.rstrip("/")):
            return "embedding"
    for ep in config.DETAILED_RERANK_ENDPOINTS:
        if p.endswith(ep.rstrip("/")):
            return "rerank"
    for ep in config.DETAILED_IMAGE_ENDPOINTS:
        if p.endswith(ep.rstrip("/")):
            return "image"
    return "general"


# ============================
#  辅助函数
# ============================

def _is_stream_request(body: bytes) -> bool:
    if not body:
        return False
    try:
        data = json.loads(body)
        return data.get("stream", False) is True
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _extract_token(request: Request, mask: bool = None) -> tuple:
    """
    从 Authorization 头提取令牌
    返回 (display_name, full_token)
    - display_name: 用于展示的名称（含令牌名称 + 脱敏密钥）
    - full_token: 完整令牌密钥（存库用于精确追踪）
    """
    if mask is None:
        mask = config.TOKEN_MASK

    auth = request.headers.get("Authorization", "")
    full_token = ""

    if auth.startswith("Bearer "):
        full_token = auth[7:].strip()
    elif auth.startswith("sk-"):
        full_token = auth.strip()
    else:
        full_token = auth.strip() if auth else "unknown"

    if not full_token:
        return ("unknown", "")

    # 尝试从 newapi 数据库查令牌名称
    resolved = token_resolver.resolve_or_key(full_token)
    display = resolved

    return (display, full_token)


def _extract_model(body: bytes) -> Optional[str]:
    try:
        data = json.loads(body)
        return data.get("model")
    except Exception:
        return None


def _extract_thinking(response_body: bytes) -> Optional[str]:
    try:
        data = json.loads(response_body)
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {}) or choices[0].get("delta", {})
            reasoning = msg.get("reasoning_content")
            if reasoning:
                return reasoning
    except Exception:
        pass
    return None


def _extract_usage(response_body: bytes) -> dict:
    try:
        data = json.loads(response_body)
        usage = data.get("usage", {})
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0) or usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    except Exception:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _extract_request_id(response_body: bytes) -> Optional[str]:
    try:
        data = json.loads(response_body)
        return data.get("id")
    except Exception:
        return None


def _safe_truncate(data: bytes, max_size: int = None) -> str:
    if max_size is None:
        max_size = config.MAX_BODY_LOG_SIZE
    if len(data) <= max_size:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return f"[binary data, {len(data)} bytes]"
    truncated = data[:max_size]
    try:
        return truncated.decode("utf-8", errors="replace") + f"\n... [truncated, total {len(data)} bytes]"
    except Exception:
        return f"[data truncated, {len(data)} bytes]"


# ============================
#  非流式请求处理
# ============================

async def _forward_normal(
    target_url: str,
    headers: dict,
    body: bytes,
    path: str,
    request: Request,
    start_time: float,
    endpoint_type: str,
):
    """转发非流式请求"""
    status_code = 502
    response_body = b""
    error_msg = None
    is_error = False

    try:
        client = get_client()
        resp = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
        status_code = resp.status_code
        response_body = resp.content

        response_headers = dict(resp.headers)
        for h in ("transfer-encoding", "content-encoding", "connection", "keep-alive"):
            response_headers.pop(h, None)

        latency = int((time.time() - start_time) * 1000)

        _log_request(path, request, body, response_body, status_code, latency,
                     endpoint_type, is_error=False)

        return FastAPIResponse(
            content=response_body,
            status_code=status_code,
            headers=response_headers,
        )

    except httpx.TimeoutException:
        status_code = 504
        error_msg = "upstream timeout"
        is_error = True
        _error_log.warning(f"Timeout: {target_url}")
    except httpx.ConnectError:
        status_code = 502
        error_msg = "upstream unreachable"
        is_error = True
        _error_log.error(f"Cannot connect to upstream: {target_url}")
    except Exception as e:
        status_code = 502
        error_msg = str(e)
        is_error = True
        _error_log.error(f"Proxy error [{target_url}]: {traceback.format_exc()}")

    latency = int((time.time() - start_time) * 1000)
    _log_request(path, request, body, b"", status_code, latency,
                 endpoint_type, is_error=True, error_msg=error_msg)
    _full_log.debug(f"[ERROR] {request.method} /{path} -> {status_code} | {error_msg} | {latency}ms")

    return PlainTextResponse(error_msg or "proxy error", status_code=status_code)


# ============================
#  流式请求处理 (SSE)
# ============================

async def _forward_stream(
    target_url: str,
    headers: dict,
    body: bytes,
    path: str,
    request: Request,
    start_time: float,
    endpoint_type: str,
):
    """转发流式请求"""
    client = get_client()
    chunks = []
    total_bytes = 0
    status_code = 200
    error_msg = None

    try:
        req = client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
        resp = await client.send(req, stream=True)

        status_code = resp.status_code
        response_headers = dict(resp.headers)
        for h in ("transfer-encoding", "content-encoding", "connection", "keep-alive"):
            response_headers.pop(h, None)

        async def stream_generator():
            nonlocal status_code, error_msg, total_bytes
            try:
                async for chunk in resp.aiter_bytes():
                    # 内存保护：流式响应超过上限后截断
                    if total_bytes < config.MAX_STREAM_BUFFER_SIZE:
                        chunks.append(chunk)
                        total_bytes += len(chunk)
                    yield chunk
            except Exception as e:
                error_msg = str(e)
                _error_log.error(f"Stream error [{target_url}]: {e}")
            finally:
                await resp.aclose()

        async def log_after_stream():
            full_body = b"".join(chunks) if chunks else b""
            latency = int((time.time() - start_time) * 1000)
            _log_request(path, request, body, full_body, status_code, latency,
                         endpoint_type, is_error=error_msg is not None,
                         error_msg=error_msg, is_stream=True)
            _full_log.debug(
                f"[STREAM] {request.method} /{path} -> {status_code} | "
                f"{len(full_body)} bytes (capped at {config.MAX_STREAM_BUFFER_SIZE}) | {latency}ms"
            )

        asyncio.create_task(log_after_stream())

        return StreamingResponse(
            stream_generator(),
            status_code=status_code,
            headers=response_headers,
        )

    except httpx.TimeoutException:
        _error_log.warning(f"Stream timeout: {target_url}")
        return PlainTextResponse("upstream timeout", status_code=504)
    except httpx.ConnectError:
        _error_log.error(f"Cannot connect to upstream: {target_url}")
        return PlainTextResponse("upstream unreachable", status_code=502)
    except Exception as e:
        _error_log.error(f"Stream proxy error [{target_url}]: {traceback.format_exc()}")
        return PlainTextResponse(str(e), status_code=502)


# ============================
#  统一日志记录
# ============================

def _log_request(
    path: str,
    request: Request,
    req_body: bytes,
    resp_body: bytes,
    status_code: int,
    latency_ms: int,
    endpoint_type: str = "general",
    is_error: bool = False,
    error_msg: Optional[str] = None,
    is_stream: bool = False,
):
    """统一日志记录入口——根据端点类型记录不同级别的详情"""
    token_display, token_full = _extract_token(request)
    model = _extract_model(req_body)
    request_id = _extract_request_id(resp_body) if resp_body else None
    thinking = _extract_thinking(resp_body) if resp_body else None
    usage = _extract_usage(resp_body) if resp_body else {}
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")[:500]

    # === 完整日志写文件 ===
    _full_log.debug(
        f"\n{'='*60}\n"
        f"ID: {request_id or 'N/A'} | Model: {model or 'N/A'} | Token: {token_display}\n"
        f"Type: {endpoint_type} | Endpoint: /{path} | Status: {status_code} | Stream: {is_stream} | Latency: {latency_ms}ms\n"
        f"IP: {ip}\n"
        f"--- REQUEST ---\n{_safe_truncate(req_body)}\n"
        f"--- RESPONSE ---\n{_safe_truncate(resp_body) if resp_body else '(empty)'}\n"
        f"{'='*60}"
    )

    # === 错误日志 ===
    if is_error:
        _error_log.error(
            f"[{endpoint_type.upper()}] /{path} | {status_code} | {token_display} | {error_msg or 'unknown'}"
        )

    # === 写入数据库（关键日志）===
    if endpoint_type == "general":
        database.enqueue_log({
            "log_type": "general",
            "endpoint": f"/{path}",
            "method": request.method,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "token_name": token_display,
            "client_ip": ip,
            "request_summary": _safe_truncate(req_body, max_size=2048),
        })
    else:
        # chat / embedding / rerank / image 都做详细记录
        database.enqueue_log({
            "log_type": "chat",
            "request_id": request_id,
            "endpoint": f"/{path}",
            "model": model,
            "token_name": token_display,
            "token_full": token_full,
            "client_ip": ip,
            "user_agent": ua,
            "request_body": _safe_truncate(req_body),
            "response_body": _safe_truncate(resp_body) if resp_body else None,
            "thinking_content": thinking,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "status_code": status_code,
            "is_stream": 1 if is_stream else 0,
            "is_error": 1 if is_error else 0,
            "error_message": error_msg,
            "latency_ms": latency_ms,
        })


# ============================
#  路由：透明代理所有请求
# ============================

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def catch_all(path: str, request: Request):
    """万能代理——所有请求原样转发到 newapi"""
    start_time = time.time()
    target_url = f"{config.NEWAPI_URL}/{path}"

    # 构造转发 headers
    headers = {}
    for k, v in request.headers.items():
        kl = k.lower()
        # 排除 hop-by-hop 头（保留 content-length，POST 请求需要）
        if kl in ("host", "transfer-encoding", "connection"):
            continue
        headers[k] = v

    # 设置正确的 Host
    from urllib.parse import urlparse
    parsed = urlparse(config.NEWAPI_URL)
    headers["host"] = parsed.netloc

    # 读取请求体
    body = await request.body()

    endpoint_type = _classify_endpoint(path)
    token_display, _ = _extract_token(request)
    ip = request.client.host if request.client else "unknown"

    _full_log.debug(
        f"[>>] {request.method} /{path} | Type: {endpoint_type} | "
        f"Token: {token_display} | IP: {ip} | "
        f"Body: {len(body)} bytes | Stream: {_is_stream_request(body)}"
    )

    if _is_stream_request(body):
        return await _forward_stream(target_url, headers, body, path, request, start_time, endpoint_type)
    else:
        return await _forward_normal(target_url, headers, body, path, request, start_time, endpoint_type)


# ============================
#  健康检查
# ============================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "upstream": config.NEWAPI_URL,
        "db_queue_size": database.db_queue.qsize(),
        "db_queue_max": config.DB_QUEUE_MAXSIZE,
    }


# ============================
#  直接启动入口
# ============================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.LISTEN_HOST,
        port=config.LISTEN_PORT,
        log_level=config.LOG_LEVEL.lower(),
    )
