---
description: Generate a fun, shareable summary of your Claude Code usage stats — like Spotify Wrapped but for AI-assisted development. Use when the user asks for their stats, summary, wrapped, or wants a fun overview of their Claude Code activity.
---

# Wrapped — Your Claude Code Year in Review

Generate a fun, visual summary of the user's Claude Code activity. Think Spotify Wrapped energy.

Run ALL queries below, then present as an engaging, shareable summary.

## Schema reference (for adapting queries)

When the user specifies a date range, add WHERE clauses. Use these join paths:

```
sessions.session_id  ←→  messages.session_id
sessions.session_id  ←→  tool_calls.session_id
messages.uuid        ←→  tool_calls.message_uuid   (NOT message_id)
```

Key column differences between tables:
- **sessions**: `created_at` (TIMESTAMP), `modified_at`
- **messages**: `timestamp` (TIMESTAMP) — NO created_at column
- **tool_calls**: `timestamp` (TIMESTAMP) — NO created_at column
- **Ambiguous columns**: `tool_name` exists in BOTH messages and tool_calls — always qualify with table alias

To filter `tool_calls` or `messages` by date, JOIN through `sessions`:
```sql
-- tool_calls filtered by date:
SELECT tc.tool_name, COUNT(*) uses
FROM tool_calls tc
JOIN sessions s ON tc.session_id = s.session_id
WHERE s.created_at >= '...' AND s.created_at < '...'
GROUP BY 1 ORDER BY 2 DESC

-- messages filtered by date:
SELECT model, COUNT(*) messages
FROM messages m
JOIN sessions s ON m.session_id = s.session_id
WHERE model IS NOT NULL AND s.created_at >= '...' AND s.created_at < '...'
GROUP BY 1 ORDER BY 2 DESC
```

## Data collection

### All-time stats
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT COUNT(*) total_sessions, SUM(message_count) total_messages, SUM(total_input_tokens + total_output_tokens) total_tokens, COUNT(DISTINCT project_name) total_projects, MIN(created_at)::DATE first_session, MAX(created_at)::DATE latest_session FROM sessions"
```

### Top projects by session count
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, COUNT(*) sessions, SUM(message_count) messages FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
```

### Favorite tools (top 10)
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT tool_name, COUNT(*) uses FROM tool_calls GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```

### Longest session ever
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, created_at::DATE, message_count, total_input_tokens + total_output_tokens as tokens, LEFT(first_prompt, 100) prompt FROM sessions ORDER BY message_count DESC LIMIT 1"
```

### Most expensive session
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, created_at::DATE, message_count, ROUND(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6, 2) as est_cost_usd, LEFT(first_prompt, 100) prompt FROM sessions ORDER BY (total_input_tokens + total_output_tokens) DESC LIMIT 1"
```

### Busiest day
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT created_at::DATE as day, COUNT(*) sessions, SUM(message_count) messages FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
```

### Models used
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT model, COUNT(*) messages FROM messages WHERE model IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
```

### Streak (consecutive days)
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "WITH days AS (SELECT DISTINCT created_at::DATE as d FROM sessions), streaks AS (SELECT d, d - ROW_NUMBER() OVER (ORDER BY d) * INTERVAL '1 day' as grp FROM days) SELECT COUNT(*) as streak_days, MIN(d)::DATE as from_date, MAX(d)::DATE as to_date FROM streaks GROUP BY grp ORDER BY streak_days DESC LIMIT 1"
```

### Session distribution by hour of day
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT EXTRACT(HOUR FROM created_at) as hour, COUNT(*) sessions FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
```

## Presentation

Present as a **fun, engaging summary** with personality. Use section headers like:

- **Your Numbers** — total sessions, messages, tokens, projects
- **#1 Project** — your most-visited project and what it says about you
- **Power Tools** — your top 5 tools and what that means
- **Marathon Session** — your longest session: what happened?
- **Most Expensive Moment** — the session that burned the most tokens
- **Peak Hours** — when you do your best AI-assisted work
- **Your Streak** — longest consecutive days using Claude Code
- **Your Type** — categorize them based on patterns:
  - "The Architect" — mostly planning, long first prompts, few sessions per project
  - "The Debugger" — lots of Bash/Read, short sessions, many per project
  - "The Builder" — Edit/Write heavy, medium sessions, steady output
  - "The Explorer" — Search/Read heavy, many projects, short sessions
  - "The Power User" — high volume, diverse tools, multiple projects per day

Keep it concise, punchy, fun. Something they'd screenshot and share.
