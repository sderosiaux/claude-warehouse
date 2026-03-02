-- claude-warehouse schema
-- DuckDB DDL for ~/.claude operational data

-- Sync watermarks for incremental ingest
CREATE TABLE IF NOT EXISTS _sync_state (
    source_name  VARCHAR PRIMARY KEY,
    last_mtime   DOUBLE,      -- max file mtime epoch seen
    last_run     TIMESTAMP DEFAULT current_timestamp,
    files_synced INTEGER DEFAULT 0,
    rows_synced  BIGINT DEFAULT 0
);

-- Session-level aggregates from projects/*.jsonl
CREATE TABLE IF NOT EXISTS sessions (
    session_id          VARCHAR PRIMARY KEY,
    project_path        VARCHAR,
    project_name        VARCHAR,
    git_branch          VARCHAR,
    version             VARCHAR,
    cwd                 VARCHAR,
    created_at          TIMESTAMP,
    modified_at         TIMESTAMP,
    message_count       INTEGER,
    total_input_tokens  BIGINT,
    total_output_tokens BIGINT,
    total_cache_read    BIGINT,
    total_cache_write   BIGINT,
    tools_used          JSON,
    models_used         JSON,
    first_prompt        VARCHAR,
    file_path           VARCHAR,
    is_subagent         BOOLEAN DEFAULT FALSE,
    parent_session_id   VARCHAR
);

-- Deleted sessions (JSONL removed, metadata from sessions-index.json)
CREATE TABLE IF NOT EXISTS deleted_sessions (
    session_id      VARCHAR PRIMARY KEY,
    project_path    VARCHAR,
    project_name    VARCHAR,
    git_branch      VARCHAR,
    created_at      TIMESTAMP,
    modified_at     TIMESTAMP,
    message_count   INTEGER,
    first_prompt    VARCHAR,
    summary         VARCHAR
);

-- Individual turns from JSONL
CREATE TABLE IF NOT EXISTS messages (
    session_id          VARCHAR,
    uuid                VARCHAR,
    parent_uuid         VARCHAR,
    type                VARCHAR,
    timestamp           TIMESTAMP,
    is_sidechain        BOOLEAN,
    role                VARCHAR,
    model               VARCHAR,
    stop_reason         VARCHAR,
    input_tokens        BIGINT,
    output_tokens       BIGINT,
    cache_read_tokens   BIGINT,
    cache_write_tokens  BIGINT,
    content_types       JSON,
    tool_name           VARCHAR,
    tool_input_summary  VARCHAR,
    text_content        VARCHAR,
    PRIMARY KEY (session_id, uuid)
);

-- Extracted tool calls from assistant content blocks
CREATE TABLE IF NOT EXISTS tool_calls (
    session_id      VARCHAR,
    message_uuid    VARCHAR,
    tool_name       VARCHAR,
    tool_input      VARCHAR,
    timestamp       TIMESTAMP,
    idx             INTEGER,
    PRIMARY KEY (session_id, message_uuid, idx)
);

-- Hook event logs from logs/*.log
CREATE TABLE IF NOT EXISTS hook_events (
    id              BIGINT,
    event_type      VARCHAR,
    session_id      VARCHAR,
    timestamp       TIMESTAMP,
    cwd             VARCHAR,
    tool_name       VARCHAR,
    tool_input      VARCHAR,
    file_path       VARCHAR,
    PRIMARY KEY (file_path, id)
);

-- Todos from todos/*.json
CREATE TABLE IF NOT EXISTS todos (
    file_name   VARCHAR,
    idx         INTEGER,
    session_id  VARCHAR,
    content     VARCHAR,
    status      VARCHAR,
    active_form VARCHAR,
    PRIMARY KEY (file_name, idx)
);

-- Debug logs from debug/*.txt
CREATE TABLE IF NOT EXISTS debug_logs (
    file_path   VARCHAR PRIMARY KEY,
    session_id  VARCHAR,
    timestamp   TIMESTAMP,
    first_line  VARCHAR,
    line_count  INTEGER,
    size_bytes  BIGINT
);

-- Research/review history from history/**/*.md
CREATE TABLE IF NOT EXISTS research_history (
    file_path   VARCHAR PRIMARY KEY,
    category    VARCHAR,
    agent       VARCHAR,
    timestamp   TIMESTAMP,
    description VARCHAR,
    content     VARCHAR
);
