"""
数据库操作模块 - 后台多线程写入 MySQL

设计：
  - 线程安全队列 → 多个 worker 线程并行写入
  - 连接池管理（减少连接开销）
  - 高并发下队列满时降级写文件，保证不丢日志
"""
import logging
import queue
import threading
from typing import List, Optional

import pymysql
from pymysql.cursors import DictCursor

import config

db_logger = logging.getLogger("db")
db_queue = queue.Queue(maxsize=config.DB_QUEUE_MAXSIZE)

# ============================
#  连接池
# ============================

class ConnectionPool:
    """简易 MySQL 连接池"""

    def __init__(self, min_size: int, max_size: int):
        self.min_size = min_size
        self.max_size = max_size
        self._pool: List[pymysql.Connection] = []
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max_size)

    def _create_conn(self) -> pymysql.Connection:
        return pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            charset='utf8mb4',
            autocommit=True,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )

    def init(self):
        """初始化连接池，创建最小连接数"""
        for _ in range(self.min_size):
            try:
                conn = self._create_conn()
                self._pool.append(conn)
            except Exception as e:
                db_logger.error(f"Pool init connection failed: {e}")
        db_logger.info(f"Connection pool initialized: {len(self._pool)} connections")

    def acquire(self) -> Optional[pymysql.Connection]:
        """获取连接"""
        self._sem.acquire()
        with self._lock:
            if self._pool:
                return self._pool.pop()
        try:
            return self._create_conn()
        except Exception as e:
            self._sem.release()
            db_logger.error(f"Pool acquire failed: {e}")
            return None

    def release(self, conn: pymysql.Connection):
        """归还连接"""
        try:
            conn.ping(reconnect=False)
            with self._lock:
                if len(self._pool) < self.max_size:
                    self._pool.append(conn)
                    self._sem.release()
                    return
        except Exception:
            pass
        # 无法归还则关闭
        try:
            conn.close()
        except Exception:
            pass
        self._sem.release()

    def close_all(self):
        """关闭所有连接"""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()


pool = ConnectionPool(
    min_size=config.DB_POOL_MIN_SIZE,
    max_size=config.DB_POOL_MAX_SIZE,
)

# ============================
#  SQL 语句
# ============================

INSERT_CHAT_SQL = """
    INSERT INTO api_chat_logs
        (request_id, session_id, endpoint, model, token_name, token_full, client_ip, user_agent,
         request_body, response_body, thinking_content,
         prompt_tokens, completion_tokens, total_tokens,
         status_code, is_stream, is_error, error_message, latency_ms)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

INSERT_GENERAL_SQL = """
    INSERT INTO api_general_logs
        (endpoint, method, status_code, latency_ms, token_name, client_ip, request_summary)
    VALUES (%s,%s,%s,%s,%s,%s,%s)
"""

# Embedding / Rerank 也做详细记录
INSERT_EMBEDDING_SQL = """
    INSERT INTO api_chat_logs
        (request_id, endpoint, model, token_name, token_full, client_ip, user_agent,
         request_body, response_body,
         prompt_tokens, completion_tokens, total_tokens,
         status_code, is_stream, is_error, error_message, latency_ms)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

# ============================
#  Worker 线程
# ============================

def _execute_sql(conn: pymysql.Connection, sql: str, params: tuple) -> bool:
    """执行 SQL，自动重连"""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            return True
        except pymysql.err.OperationalError:
            if attempt < max_retries - 1:
                try:
                    conn.ping(reconnect=True)
                except Exception:
                    pass
            else:
                raise
        except Exception:
            raise
    return False


def _write_chat(item: dict):
    """写入聊天日志"""
    conn = pool.acquire()
    if conn is None:
        return False
    try:
        _execute_sql(conn, INSERT_CHAT_SQL, (
            item.get("request_id"),
            item.get("session_id"),
            item.get("endpoint"),
            item.get("model"),
            item.get("token_name"),
            item.get("token_full"),
            item.get("client_ip"),
            item.get("user_agent"),
            item.get("request_body"),
            item.get("response_body"),
            item.get("thinking_content"),
            item.get("prompt_tokens", 0),
            item.get("completion_tokens", 0),
            item.get("total_tokens", 0),
            item.get("status_code"),
            item.get("is_stream", 0),
            item.get("is_error", 0),
            item.get("error_message"),
            item.get("latency_ms"),
        ))
        return True
    except Exception as e:
        db_logger.error(f"Write chat log failed: {e}")
        return False
    finally:
        pool.release(conn)


def _write_general(item: dict):
    """写入通用日志"""
    conn = pool.acquire()
    if conn is None:
        return False
    try:
        _execute_sql(conn, INSERT_GENERAL_SQL, (
            item.get("endpoint"),
            item.get("method"),
            item.get("status_code"),
            item.get("latency_ms"),
            item.get("token_name"),
            item.get("client_ip"),
            item.get("request_summary"),
        ))
        return True
    except Exception as e:
        db_logger.error(f"Write general log failed: {e}")
        return False
    finally:
        pool.release(conn)


def db_worker(worker_id: int):
    """后台工作线程：从队列取日志，写入 MySQL"""
    db_logger.info(f"DB worker {worker_id} started")
    while True:
        item = db_queue.get()
        if item is None:  # 关闭信号
            db_queue.put(None)  # 传递给下一个 worker
            break
        try:
            log_type = item.get("log_type", "chat")
            if log_type == "chat":
                _write_chat(item)
            else:
                _write_general(item)
        except Exception as e:
            db_logger.error(f"Worker {worker_id} unexpected error: {e}")
        finally:
            db_queue.task_done()
    db_logger.info(f"DB worker {worker_id} stopped")


# ============================
#  公共接口
# ============================

_workers: List[threading.Thread] = []


def start_db_workers():
    """启动多个后台数据库写入线程"""
    pool.init()
    for i in range(config.DB_WORKER_THREADS):
        t = threading.Thread(
            target=db_worker, args=(i,),
            daemon=True, name=f"db-logger-{i}"
        )
        t.start()
        _workers.append(t)
    db_logger.info(f"Started {len(_workers)} DB worker threads")


def enqueue_log(log_entry: dict):
    """将日志条目放入队列（非阻塞）"""
    try:
        db_queue.put_nowait(log_entry)
    except queue.Full:
        # 队列满了写文件降级
        db_logger.warning(
            f"DB queue full ({config.DB_QUEUE_MAXSIZE}), "
            f"dropping entry for endpoint={log_entry.get('endpoint', '?')}"
        )


def shutdown():
    """优雅关闭：发送停止信号，等待队列清空（最多等 10 秒）"""
    db_logger.info("Shutting down DB workers...")
    db_queue.put(None)  # 发送一个停止信号，worker 会级联传递

    # 等待所有 worker 结束
    for t in _workers:
        t.join(timeout=10)

    # 处理队列中剩余条目（尽力而为）
    remaining = 0
    while not db_queue.empty():
        try:
            item = db_queue.get_nowait()
            if item is None:
                continue
            if item.get("log_type") == "chat":
                _write_chat(item)
            else:
                _write_general(item)
            remaining += 1
        except queue.Empty:
            break
    if remaining > 0:
        db_logger.info(f"Processed {remaining} remaining log entries during shutdown")

    pool.close_all()
    db_logger.info("DB workers shut down complete")
