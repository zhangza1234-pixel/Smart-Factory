-- Run against the database selected by the caller. Existing tables are preserved.
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    industry VARCHAR(200) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS plants (
    plant_id VARCHAR(100) PRIMARY KEY,
    tenant_id VARCHAR(100) NOT NULL,
    name VARCHAR(200) NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    INDEX idx_plants_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS production_lines (
    line_id VARCHAR(100) PRIMARY KEY,
    plant_id VARCHAR(100) NOT NULL,
    name VARCHAR(200) NOT NULL,
    FOREIGN KEY (plant_id) REFERENCES plants(plant_id),
    INDEX idx_lines_plant (plant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS device_parameters (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    line_id VARCHAR(100) NOT NULL,
    device_name VARCHAR(200) NOT NULL,
    parameter VARCHAR(100) NOT NULL,
    value VARCHAR(200) NOT NULL,
    tolerance VARCHAR(100) NOT NULL DEFAULT '',
    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (line_id) REFERENCES production_lines(line_id),
    UNIQUE KEY uk_device_parameter (line_id, device_name, parameter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fault_knowledge (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(100) NOT NULL,
    device_type VARCHAR(200) NOT NULL,
    code VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    solution TEXT NOT NULL,
    severity VARCHAR(50) NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    UNIQUE KEY uk_fault (tenant_id, device_type, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Relational document catalog; uploading RAG documents currently writes to Chroma only.
CREATE TABLE IF NOT EXISTS process_documents (
    doc_id VARCHAR(100) PRIMARY KEY,
    line_id VARCHAR(100) NOT NULL,
    title VARCHAR(200) NOT NULL,
    category VARCHAR(100) NOT NULL,
    access_level ENUM('操作员', '工程师', '专家') NOT NULL,
    FOREIGN KEY (line_id) REFERENCES production_lines(line_id),
    INDEX idx_documents_line_category (line_id, category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
