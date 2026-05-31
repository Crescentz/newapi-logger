-- ============================================================
-- newapi-logger 数据库初始化脚本 v2.0
--
-- 使用方法:
--   方式1 (宿主机): mysql -h 127.0.0.1 -P 33060 -u root -p < schema.sql
--   方式2 (容器内): docker exec -i <mysql容器名> mysql -u root -p < schema.sql
--   方式3 (交互式): docker exec -it <mysql容器名> mysql -u root -p
--                  然后 source /path/to/schema.sql;
-- ============================================================

CREATE DATABASE IF NOT EXISTS newapi_logs
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE newapi_logs;

-- ============================================================
-- 核心表: API 调用详细日志
-- 记录所有模型的调用详情（LLM / embedding / rerank / 图像生成）
-- key-value 对应: token_full → 精确追踪到 newapi 的哪个令牌
-- ============================================================
CREATE TABLE IF NOT EXISTS api_chat_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 请求标识
    request_id      VARCHAR(128)    COMMENT 'API 返回的 id（如 chatcmpl-xxx）',
    session_id      VARCHAR(64)     COMMENT '会话 ID（多轮对话关联，X-Conversation-ID 或自动生成）',
    endpoint        VARCHAR(256)    NOT NULL COMMENT '请求端点路径',
    model           VARCHAR(128)    COMMENT '模型名称',

    -- 用户/令牌信息 (关键！用于追踪用户对话)
    token_name      VARCHAR(256)    COMMENT '令牌显示名（脱敏）',
    token_full      VARCHAR(512)    COMMENT '令牌完整值（用于精确追踪）',
    client_ip       VARCHAR(64)     COMMENT '客户端 IP',
    user_agent      TEXT            COMMENT '客户端 User-Agent',

    -- 核心内容
    request_body    LONGTEXT        COMMENT '完整请求体 JSON（含 prompt / messages / input 等）',
    response_body   LONGTEXT        COMMENT '完整响应体 JSON（含 choices / data / answer 等）',
    thinking_content LONGTEXT       COMMENT '思考过程（reasoning_content，仅 reasoning 模型有）',

    -- Token 统计
    prompt_tokens       INT DEFAULT 0   COMMENT '输入 token 数',
    completion_tokens   INT DEFAULT 0   COMMENT '输出 token 数',
    total_tokens        INT DEFAULT 0   COMMENT '总 token 数',

    -- 状态信息
    status_code     INT             COMMENT 'HTTP 状态码',
    is_stream       TINYINT(1) DEFAULT 0 COMMENT '是否流式请求',
    is_error        TINYINT(1) DEFAULT 0 COMMENT '是否出错',
    error_message   TEXT            COMMENT '错误信息',
    latency_ms      INT             COMMENT '响应延迟（毫秒）',

    -- 元数据
    created_at      DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录创建时间',

    -- 索引
    INDEX idx_created_at (created_at),
    INDEX idx_model (model),
    INDEX idx_token (token_name),
    INDEX idx_token_full (token_full(64)),
    INDEX idx_request_id (request_id),
    INDEX idx_session_id (session_id),
    INDEX idx_is_error (is_error),
    INDEX idx_endpoint (endpoint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='API 调用详细日志（支持所有模型类型）';


-- ============================================================
-- 辅助表: 通用 API 调用记录
-- 记录未被详细记录的端点（管理类、健康检查等）
-- ============================================================
CREATE TABLE IF NOT EXISTS api_general_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    endpoint        VARCHAR(256)    NOT NULL COMMENT '请求端点',
    method          VARCHAR(10)     NOT NULL COMMENT 'HTTP 方法',
    status_code     INT             COMMENT 'HTTP 状态码',
    latency_ms      INT             COMMENT '响应延迟（毫秒）',
    token_name      VARCHAR(256)    COMMENT 'API 令牌名称',
    client_ip       VARCHAR(64)     COMMENT '客户端 IP',
    request_summary TEXT            COMMENT '请求摘要（截取 body 前 2KB）',
    created_at      DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),

    INDEX idx_created_at (created_at),
    INDEX idx_endpoint (endpoint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='通用 API 调用简要记录';
