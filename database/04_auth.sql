-- Additive authentication migration; no default accounts or passwords.
CREATE TABLE IF NOT EXISTS auth_users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(250) NOT NULL,
    tenant_id VARCHAR(100) NOT NULL,
    plant_id VARCHAR(100) NOT NULL,
    line_id VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL,
    active INTEGER NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until DOUBLE NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    expires_at DOUBLE NOT NULL,
    INDEX ix_auth_sessions_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
