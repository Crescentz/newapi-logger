"""
令牌名称解析器 (SQLite 版)

newapi 使用 calciumion/new-api 镜像，数据存储在 SQLite 文件中。
本模块只读访问该 SQLite 文件，提取令牌密钥 → 名称的映射。

newapi SQLite 表结构（one-api 格式）:
  tokens 表: id, user_id, key, name, status, created_time, ...
  status=1 表示令牌有效
"""
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, Optional

import config

_resolver_log = logging.getLogger("token")
_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()
_last_refresh = 0.0
_enabled = False

# newapi SQLite 数据库路径（挂载的只读卷）
DB_PATH = config.NEWAPI_SQLITE_PATH


def _query_newapi_tokens() -> Dict[str, str]:
    """从 newapi SQLite 数据库读取所有有效令牌（key → name）"""
    result = {}
    if not os.path.exists(DB_PATH):
        _resolver_log.warning(f"newapi SQLite not found at: {DB_PATH}")
        return result

    conn = None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 先探测表名（兼容 one-api / new-api 不同版本）
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%token%'")
        tables = [r[0] for r in cur.fetchall()]
        table_name = "tokens" if "tokens" in tables else (tables[0] if tables else None)

        if not table_name:
            _resolver_log.warning("No token table found in newapi SQLite")
            return result

        # 探测列名
        cur.execute(f"PRAGMA table_info(`{table_name}`)")
        columns = {r["name"] for r in cur.fetchall()}

        # 适配不同版本的列名
        key_col = "key"
        name_col = "name" if "name" in columns else None
        if name_col is None:
            # 尝试其他可能的名称列
            for candidate in ("name", "token_name", "description", "remark"):
                if candidate in columns:
                    name_col = candidate
                    break

        if name_col is None:
            _resolver_log.warning(f"Token table '{table_name}' has no name column. Columns: {columns}")
            return result

        # 查询有效令牌
        cur.execute(
            f'SELECT `{key_col}`, `{name_col}` FROM `{table_name}` WHERE `status` = 1'
        )
        for row in cur.fetchall():
            key = str(row[0]).strip() if row[0] else ""
            name = str(row[1]).strip() if row[1] else ""
            if key and name:
                result[key] = name

        _resolver_log.info(f"Token cache refreshed from SQLite: {len(result)} tokens loaded")

    except sqlite3.OperationalError as e:
        _resolver_log.warning(f"SQLite read error (DB may be locked by newapi): {e}")
    except Exception as e:
        _resolver_log.error(f"Token query failed: {e}")
    finally:
        if conn:
            conn.close()

    return result


def init():
    """初始化令牌名称缓存"""
    global _enabled
    if config.TOKEN_NAME_CACHE_TTL <= 0:
        _resolver_log.info("Token name resolver disabled (TOKEN_NAME_CACHE_TTL=0)")
        return

    _refresh()
    _enabled = True
    _resolver_log.info(
        f"Token name resolver enabled (SQLite: {DB_PATH}), cache TTL={config.TOKEN_NAME_CACHE_TTL}s"
    )


def _refresh():
    global _cache, _last_refresh
    tokens = _query_newapi_tokens()
    if tokens:
        with _cache_lock:
            _cache = tokens
            _last_refresh = time.time()


def _maybe_refresh():
    if time.time() - _last_refresh > config.TOKEN_NAME_CACHE_TTL:
        t = threading.Thread(target=_refresh, daemon=True)
        t.start()


def resolve(token_key: str) -> Optional[str]:
    """根据令牌密钥查询名称，查不到返回 None"""
    if not _enabled or not token_key:
        return None
    _maybe_refresh()
    with _cache_lock:
        return _cache.get(token_key)


def resolve_or_key(token_key: str) -> str:
    """
    查询令牌名称，查不到返回脱敏密钥
    返回: "张三的令牌 (sk-abc...f456)" 或 "sk-abc...f456"
    """
    name = resolve(token_key)
    if name:
        if len(token_key) > 20:
            key_preview = token_key[:8] + "..." + token_key[-8:]
        else:
            key_preview = token_key
        return f"{name} ({key_preview})"
    return token_key
