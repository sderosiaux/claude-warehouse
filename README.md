# claude-warehouse

> Your Claude Code sessions are gold. Stop throwing them away.

Every session with Claude Code produces knowledge — solutions, debugging paths, architecture decisions, tool patterns. When the session ends, it all evaporates. Git stores *what* changed. Claude Warehouse stores *how you got there*.

It syncs every session into a local DuckDB database. Search it, query it, learn from it. The longer you use it, the more valuable it gets.

## Install

```
/plugin marketplace add sderosiaux/claude-plugins
/plugin install claude-warehouse@sderosiaux-claude-plugins
```

That's it. Sync runs automatically at every session start. No config.

**Prerequisites**: [uv](https://docs.astral.sh/uv/) (DuckDB installs automatically via uv)

## What You Get

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
