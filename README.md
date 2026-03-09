# claude-warehouse

> Your Claude Code sessions are gold. Stop throwing them away.

Every session with Claude Code produces knowledge — solutions, debugging paths, architecture decisions, tool patterns. When the session ends, it all evaporates. Git stores *what* changed. Claude Warehouse stores *how you got there*.

It syncs every session into a local DuckDB database. Search it, query it, visualize it. The longer you use it, the more valuable it gets.

## Install

```
/plugin marketplace add sderosiaux/claude-plugins
/plugin install claude-warehouse@sderosiaux-claude-plugins
```

That's it. The dashboard launches automatically at every session start.

**Prerequisites**: [uv](https://docs.astral.sh/uv/) (DuckDB installs automatically via uv)

### Background sync (recommended)

Set up a launchd daemon to sync sessions every 10 minutes in the background:

```bash
cat > ~/Library/LaunchAgents/com.claude.warehouse.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.warehouse</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>timeout 240 uv run --script "$(claude info plugins-dir)/cache/sderosiaux-claude-plugins/claude-warehouse/*/scripts/sync.py" --verbose</string>
    </array>
    <key>StartInterval</key>
    <integer>600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>LowPriorityIO</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>ExitTimeOut</key>
    <integer>300</integer>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/claude-warehouse-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-warehouse-sync.log</string>
</dict>
</plist>
EOF
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.claude.warehouse.plist
```

You can also trigger a manual sync anytime:

```bash
uv run --script "$(claude info plugins-dir)/cache/sderosiaux-claude-plugins/claude-warehouse/*/scripts/sync.py" --verbose
```

## What You Get

### Dashboard — live analytics at a glance

A visual dashboard auto-launches with every Claude Code session at `http://localhost:3141`.

- Overview cards — sessions, messages, tokens, projects, cost (30d vs prev 30d)
- Daily activity bar chart (14d)
- Cost breakdown by project (top 10)
- Tool distribution donut chart
- Write/Read ratio trend (weekly)
- Session efficiency buckets (abandoned → long)
- First-prompt quality vs. cost
- Recent sessions table
- Wrapped card — streak, peak hours, dev type, marathon session

No setup. Refreshes every 60s. Idempotent — starting multiple Claude Code sessions won't crash it.

### Recall — find what you solved before

You ask Claude naturally. It searches your history.

- "I hit a CORS error last week, how did I fix it?" → searches for `CORS error`
- "Which project had the Kafka consumer work?" → searches for `kafka consumer`
- "I built a data table component recently" → searches for `react table`

### Report — understand how you use AI

Zero-effort analytics on your AI-assisted development habits.

```
/claude-warehouse:report
```

Shows you: token costs per project, session efficiency trends, most-used tools, busiest projects, abandoned sessions, and actionable suggestions to improve your workflow.

### Costs — know where your tokens go

```
/claude-warehouse:costs
```

Token usage mapped to actual dollar amounts. Per project, per week, per model. See which projects are expensive and why.

### Wrapped — your AI coding stats, shareable

```
/claude-warehouse:wrapped
```

Your personal "Claude Code Wrapped": total sessions, tokens consumed, favorite tools, longest session, most active project, top prompts. Fun to share, useful to reflect on.

### Query — raw SQL for anything

```
/claude-warehouse:query SELECT project_name, COUNT(*) sessions FROM sessions WHERE created_at >= current_date - INTERVAL '7 days' GROUP BY 1 ORDER BY 2 DESC
```

Full SQL access to the entire warehouse.

## Architecture

```
SessionStart hook
  └── dashboard.py & → HTTP server on :3141

launchd (every 10min)
  └── sync.py        → incremental ETL into DuckDB

Browser → localhost:3141
  ├── GET /              → Chart.js single-page dashboard
  ├── GET /api/overview  → 30d summary + prev 30d comparison
  ├── GET /api/costs     → per-project token costs + USD estimates
  ├── GET /api/tools     → tool usage distribution
  ├── GET /api/sessions  → recent sessions list
  ├── GET /api/trends    → daily activity + write/read ratio weekly
  ├── GET /api/efficiency→ session shape buckets + first-prompt quality
  └── GET /api/wrapped   → streak, peak hours, marathon, dev type
```

- **Server**: Python `http.server`, uv inline deps (PEP 723), DuckDB read-only
- **Frontend**: Single HTML file, Chart.js via CDN, fetch-based, auto-refresh 60s
- **Port**: 3141, idempotent (skips if already running)

## Why This Matters

### The knowledge Git doesn't capture

| Layer | Example | Git? | Warehouse? |
|---|---|---|---|
| **Artifact** | The final code | Yes | Yes |
| **Rationale** | Why this approach, not that one | Partial (PR) | Yes |
| **Process** | The 6 things tried before the fix | No | Yes |
| **Context** | What was known/assumed at decision time | No | Yes |

### Compound value

**Month 1** — It's a search tool. You find that Docker networking fix from last week.

**Month 3** — It's a pattern detector. You notice you've debugged the same TypeScript type error 4 times. You write a lint rule.

**Month 6** — It's an implicit runbook. New error? Query your history. 40% chance you've seen something similar.

**Month 12** — It's organizational memory. You can trace how your understanding of a system evolved, which decisions held up, which were revised.

### The insights that surprise you

- **Expensive sessions ≠ productive sessions.** Your longest sessions are often spiraling loops. Your shortest are surgical.
- **Your first prompt determines everything.** Sessions with precise first prompts use 5x fewer tokens.
- **Your edit/read ratio reveals your AI maturity.** More reads = Claude is exploring for you. More edits = you're directing with precision.
- **Context switching has a measurable cost.** Same project all day = high cache hits, low token spend. Jumping between projects = re-explaining everything.

## How It Compares

| | CLAUDE.md | Memory files | claude-warehouse |
|---|---|---|---|
| What's stored | What you write | What Claude writes | Everything |
| Queryable | No | No | Full SQL |
| Automatic | No | Partial | Fully |
| Cross-project | No | No | Yes |
| Token tracking | No | No | Yes |
| Visual dashboard | No | No | Yes |
| Compounds over time | No | Slowly | Yes |

## Query Cookbook

**What did I work on this week?**
```sql
SELECT project_name, COUNT(*) sessions, SUM(message_count) msgs
FROM sessions WHERE created_at >= current_date - INTERVAL '7 days'
GROUP BY 1 ORDER BY 2 DESC
```

**Prompt quality vs. session cost:**
```sql
SELECT LENGTH(first_prompt) prompt_len, message_count,
  total_input_tokens + total_output_tokens as total_tokens
FROM sessions WHERE message_count > 3
ORDER BY total_tokens DESC LIMIT 20
```

**Tool distribution (what's Claude actually doing?):**
```sql
SELECT tool_name, COUNT(*) calls
FROM tool_calls GROUP BY 1 ORDER BY 2 DESC LIMIT 15
```

**Abandoned sessions (where AI failed you):**
```sql
SELECT project_name, first_prompt, message_count,
  total_input_tokens + total_output_tokens as wasted_tokens
FROM sessions WHERE message_count <= 3
  AND total_input_tokens + total_output_tokens > 10000
ORDER BY wasted_tokens DESC LIMIT 10
```

**Edit/Read ratio over time (AI maturity metric):**
```sql
SELECT DATE_TRUNC('week', timestamp) as week,
  COUNT(*) FILTER (WHERE tool_name = 'Edit') as edits,
  COUNT(*) FILTER (WHERE tool_name = 'Read') as reads,
  ROUND(COUNT(*) FILTER (WHERE tool_name = 'Edit')::FLOAT /
    NULLIF(COUNT(*) FILTER (WHERE tool_name = 'Read'), 0), 2) as edit_read_ratio
FROM tool_calls GROUP BY 1 ORDER BY 1 DESC LIMIT 12
```

**Daily token spend in dollars (Sonnet pricing):**
```sql
SELECT created_at::DATE as day,
  COUNT(*) sessions,
  SUM(total_input_tokens) as input_tok,
  SUM(total_output_tokens) as output_tok,
  ROUND(SUM(total_input_tokens) * 3.0 / 1000000 + SUM(total_output_tokens) * 15.0 / 1000000, 2) as cost_usd
FROM sessions GROUP BY 1 ORDER BY 1 DESC LIMIT 14
```

## Schema

| Table | Description |
|---|---|
| `sessions` | One row per session — project, branch, timestamps, token counts, first prompt, tools/models used |
| `messages` | Every conversation turn — role, content, model, token counts |
| `tool_calls` | Every tool invocation — name, input, timestamp |
| `hook_events` | Hook event logs |
| `research_history` | Research/review artifacts |
| `deleted_sessions` | Metadata from removed sessions |
| `todos` | Task items from sessions |
| `debug_logs` | Debug log metadata |

## License

MIT
