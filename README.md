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

Sync your session data into DuckDB (run once, then periodically):

```bash
./scripts/sync.py -v
```

The database is created at `~/.claude/claude.duckdb`.

For automatic sync, add a hook in your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "/path/to/claude-warehouse/scripts/sync.py"
      }]
    }]
  }
}
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
