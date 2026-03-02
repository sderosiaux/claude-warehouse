---
description: Run raw SQL queries against the Claude Code DuckDB warehouse. Use when you need precise, structured lookups across sessions, messages, tool calls, hook events, or research history that go beyond simple text search.
---

# Query — Raw SQL on the Warehouse

Run arbitrary SQL against the local DuckDB warehouse containing all Claude Code session data.

## Usage

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "$ARGUMENTS"
```

## Schema

**sessions** — One row per session
- `session_id`, `project_path`, `project_name`, `git_branch`, `version`, `cwd`
- `created_at`, `modified_at`, `message_count`
- `total_input_tokens`, `total_output_tokens`, `total_cache_read`, `total_cache_write`
- `tools_used` (JSON array), `models_used` (JSON array)
- `first_prompt`, `file_path`
- `is_subagent`, `parent_session_id`

**messages** — Individual turns from session JSONL
- `session_id`, `uuid`, `parent_uuid`, `type`, `timestamp`
- `is_sidechain`, `role`, `model`, `stop_reason`
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`
- `content_types` (JSON), `tool_name`, `tool_input_summary`, `text_content`

**tool_calls** — Extracted tool invocations
- `session_id`, `message_uuid`, `tool_name`, `tool_input`, `timestamp`, `idx`

**hook_events** — Hook event logs
- `id`, `event_type`, `session_id`, `timestamp`, `cwd`, `tool_name`, `tool_input`, `file_path`

**research_history** — Research/review artifacts
- `file_path`, `category`, `agent`, `timestamp`, `description`, `content`

**deleted_sessions** — Metadata from removed sessions
- `session_id`, `project_path`, `project_name`, `git_branch`, `created_at`, `modified_at`, `message_count`, `first_prompt`, `summary`

## Example queries

Token usage last 7 days:
```sql
SELECT project_name, COUNT(*) sessions, SUM(total_input_tokens + total_output_tokens) total FROM sessions WHERE created_at >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY total DESC
```

Most used tools:
```sql
SELECT tool_name, COUNT(*) calls FROM tool_calls WHERE timestamp >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY 2 DESC LIMIT 20
```

Sessions for a project:
```sql
SELECT session_id, created_at, message_count, first_prompt FROM sessions WHERE project_name ILIKE '%kafka%' ORDER BY created_at DESC LIMIT 10
```
