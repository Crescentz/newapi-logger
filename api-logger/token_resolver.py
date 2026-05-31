"""
令牌名称解析器

从 newapi 自身数据库中查询令牌密钥 → 令牌名称的映射。
缓存到内存，定期刷新，查询失败时静默降级（返回原始密钥）。
"""
import logging
import threading
import time
from typing import Dict, Optional

import pymysql

import config

_resolver_log = logging.getLogger("token")
_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()
_last_refresh = 0.0
_enabled = False


def _query_newapi_tokens() -> Dict[str, str]:
    """查询 newapi 数据库中的所有令牌（密钥 → 名称）"""
    result = {}
    try:
        conn = pymysql.connect(
            host=config.NEWAPI_DB_HOST,
            port=config.NEWAPI_DB_PORT,
            user=config.NEWAPI_DB_USER,
            password=config.NEWAPI_DB_PASSWORD,
            database=config.NEWAPI_DB_NAME,
            charset='utf8mb4',
            connect_timeout=5,
            read_timeout=10,
        )
        with conn.cursor() as cur:
            # newapi/one-api 的令牌表名通常是 `tokens`，字段 `key` + `name`
            cur.execute("SELECT `key`, `name` FROM `tokens` WHERE `status` = 1")
            for row in cur.fetchall():
                key = str(row[0]).strip() if row[0] else ""
                name = str(row[1]).strip() if row[1] else ""
                if key and name:
                    result[key] = name
        conn.close()
        _resolver_log.info(f"Token cache refreshed: {len(result)} tokens loaded from newapi DB")
    except pymysql.err.ProgrammingError as e:
        # 表名或字段名不对
        _resolver_log.warning(f"newapi DB schema mismatch: {e}")
    except pymysql.err.OperationalError as e:
        _resolver_log.warning(f"Cannot connect to newapi DB: {e}")
    except Exception as e:
        _resolver_log.error(f"Token query failed: {e}")
    return result


def init():
    """初始化令牌名称缓存"""
    global _enabled
    if config.TOKEN_NAME_CACHE_TTL <= 0:
        _resolver_log.info("Token name resolver disabled (TOKEN_NAME_CACHE_TTL=0)")
        return

    # 首次同步加载
    _refresh()
    _enabled = True
    _resolver_log.info(f"Token name resolver enabled, cache TTL={config.TOKEN_NAME_CACHE_TTL}s")


def _refresh():
    """刷新缓存"""
    global _cache, _last_refresh
    tokens = _query_newapi_tokens()
    if tokens:
        with _cache_lock:
            _cache = tokens
            _last_refresh = time.time()


def _maybe_refresh():
    """按 TTL 定时刷新（被动触发）"""
    if time.time() - _last_refresh > config.TOKEN_NAME_CACHE_TTL:
        t = threading.Thread(target=_refresh, daemon=True)
        t.start()


def resolve(token_key: str) -> Optional[str]:
    """
    根据令牌密钥查询名称

    参数:
        token_key: 完整的令牌密钥（如 sk-abc123def456）

    返回:
        令牌名称（如"张三的令牌"），查不到返回 None
    """
    if not _enabled or not token_key:
        return None

    _maybe_refresh()

    with _cache_lock:
        name = _cache.get(token_key)
        if name:
            return name
        # 兼容 sk- 前缀的变体
        for k, v in _cache.items():
            if k == token_key or token_key == k:
                return v

    return None


def resolve_or_key(token_key: str) -> str:
    """
    查询令牌名称，查不到返回原始密钥（脱敏后）

    返回格式: "张三的令牌 (sk-abc...f456)" 或 "sk-abc...f456"
    """
    name = resolve(token_key)
    if name:
        # 截取原始密钥片段
        if len(token_key) > 20:
            key_preview = token_key[:8] + "..." + token_key[-8:]
        else:
            key_preview = token_key
        return f"{name} ({key_preview})"
    return token_key
