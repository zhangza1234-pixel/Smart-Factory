-- Additive RAG catalog and memory tables; existing business data is preserved.

CREATE TABLE IF NOT EXISTS rag_document_families (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE IF NOT EXISTS rag_document_versions (
	id VARCHAR(64) NOT NULL, 
	family_id VARCHAR(64) NOT NULL, 
	family_name VARCHAR(200) NOT NULL, 
	version VARCHAR(80) NOT NULL, 
	tenant_id VARCHAR(100) NOT NULL, 
	plant_id VARCHAR(100) NOT NULL, 
	line_id VARCHAR(100) NOT NULL, 
	access_level VARCHAR(20) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	filename VARCHAR(300) NOT NULL, 
	category VARCHAR(100) NOT NULL DEFAULT '', 
	effective_from VARCHAR(10) NOT NULL, 
	effective_to VARCHAR(10) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	supersedes VARCHAR(64) NOT NULL, 
	chunk_count INTEGER NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_rag_family_version UNIQUE (family_id, version)
)

;


CREATE TABLE IF NOT EXISTS rag_messages (
	session_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	content TEXT NOT NULL, 
	sources TEXT NOT NULL, 
	PRIMARY KEY (session_id, ordinal)
)

;


CREATE TABLE IF NOT EXISTS rag_sessions (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	summary TEXT NOT NULL, 
	summary_through INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	lease VARCHAR(36) NOT NULL, 
	busy_until FLOAT NOT NULL, 
	updated_at FLOAT NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE IF NOT EXISTS rag_user_memories (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	content TEXT NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	valid_until VARCHAR(10) NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id)
)

;