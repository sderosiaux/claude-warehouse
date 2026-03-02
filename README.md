# claude-warehouse

Long-term memory for Claude Code. Search and query across all past sessions via DuckDB.

## What it does

Claude Code stores session data as JSONL files in `~/.claude/projects/`. This plugin syncs that data into a local DuckDB database and exposes two commands for recall:

- **`/claude-warehouse:recall <query>`** — Full-text search across all past sessions. Find previous solutions, conversations, and patterns.
- **`/claude-warehouse:query <sql>`** — Raw SQL against the warehouse for precise lookups.

## Install

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package runner)
- DuckDB is installed automatically via uv

### As a plugin (recommended)

```bash
claude --plugin-dir /path/to/claude-warehouse
```

### Via marketplace

```
/plugin marketplace add sderosiaux/claude-plugins
/plugin install claude-warehouse@sderosiaux-claude-plugins
```

## Setup

No manual setup needed. The plugin automatically syncs your session data into DuckDB (`~/.claude/claude.duckdb`) at the start of every Claude Code session.

For a manual full re-sync: `./scripts/sync.py --full -v`

## Examples

### Recall — search across past sessions

You ask Claude something, and Claude uses `recall` to search your history:

- "Have I implemented JWT auth before?" → Claude runs `/claude-warehouse:recall authentication`
- "I hit a CORS error last week, how did I fix it?" → Claude runs `/claude-warehouse:recall CORS error`
- "Which project had the Kafka consumer work?" → Claude runs `/claude-warehouse:recall kafka consumer`
- "I built a data table component recently, find it" → Claude runs `/claude-warehouse:recall react table`

### Query — SQL power moves

**What did I work on this week?**
```
/claude-warehouse:query SELECT project_name, COUNT(*) sessions, SUM(message_count) msgs FROM sessions WHERE created_at >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY 2 DESC
```

**My most used tools across all sessions:**
```
/claude-warehouse:query SELECT tool_name, COUNT(*) calls FROM tool_calls GROUP BY 1 ORDER BY 2 DESC LIMIT 15
```

**Total tokens burned per project (last 30 days):**
```
/claude-warehouse:query SELECT project_name, SUM(total_input_tokens + total_output_tokens) as tokens FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' GROUP BY 1 ORDER BY 2 DESC
```

**Find sessions where I used a specific tool:**
```
/claude-warehouse:query SELECT s.project_name, s.created_at::DATE, s.first_prompt FROM sessions s WHERE s.session_id IN (SELECT DISTINCT session_id FROM tool_calls WHERE tool_name = 'WebSearch') ORDER BY s.created_at DESC LIMIT 10
```

**Long sessions (most back-and-forth):**
```
/claude-warehouse:query SELECT project_name, created_at::DATE, message_count, LEFT(first_prompt, 80) prompt FROM sessions ORDER BY message_count DESC LIMIT 10
```

**Daily usage pattern:**
```
/claude-warehouse:query SELECT created_at::DATE as day, COUNT(*) sessions, SUM(message_count) msgs FROM sessions GROUP BY 1 ORDER BY 1 DESC LIMIT 14
```

## Schema

| Table | Description |
|---|---|
| `sessions` | One row per session with metadata, token counts, first prompt |
| `messages` | Individual conversation turns with text content |
| `tool_calls` | Every tool invocation with name and input |
| `hook_events` | Hook event logs |
| `research_history` | Research/review artifacts |
| `deleted_sessions` | Metadata from removed sessions |
| `todos` | Task items from sessions |
| `debug_logs` | Debug log metadata |

## License

MIT
