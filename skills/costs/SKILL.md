---
description: Show token usage and estimated costs in dollars for Claude Code sessions. Use when the user asks about spending, costs, token usage, budget, or wants to know how much their AI-assisted development costs. Breaks down by project, model, and time period.
---

# Costs — Token Economics Dashboard

Show token usage mapped to actual dollar amounts.

Run ALL of the following queries and present a clear cost breakdown.

## Pricing reference (as of 2025)

Use these rates for estimation:
- **Claude Sonnet**: $3/MTok input, $15/MTok output
- **Claude Opus**: $15/MTok input, $75/MTok output
- **Claude Haiku**: $0.25/MTok input, $1.25/MTok output
- Cache reads are ~90% cheaper than input tokens

Note: actual costs depend on the user's plan (Pro/Max/API). Present as estimates.

## 1. Total spend by period

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT DATE_TRUNC('week', created_at)::DATE as week, COUNT(*) sessions, SUM(total_input_tokens) input_tok, SUM(total_output_tokens) output_tok, SUM(total_cache_read) cache_tok, ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2) as est_cost_usd FROM sessions GROUP BY 1 ORDER BY 1 DESC LIMIT 12"
```

## 2. Cost per project (last 30 days)

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, COUNT(*) sessions, SUM(total_input_tokens + total_output_tokens) total_tok, ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2) as est_cost_usd, ROUND(SUM(total_cache_read)::FLOAT / NULLIF(SUM(total_input_tokens + total_cache_read), 0) * 100, 1) as cache_pct FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' GROUP BY 1 ORDER BY est_cost_usd DESC"
```

## 3. Cost per session (most expensive)

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, created_at::DATE, message_count, total_input_tokens + total_output_tokens as total_tok, ROUND(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6, 2) as est_cost_usd, LEFT(first_prompt, 80) as prompt FROM sessions ORDER BY est_cost_usd DESC LIMIT 10"
```

## 4. Token waste: abandoned high-cost sessions

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, created_at::DATE, message_count, ROUND(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6, 2) as wasted_usd, LEFT(first_prompt, 80) as prompt FROM sessions WHERE message_count <= 3 AND (total_input_tokens + total_output_tokens) > 10000 ORDER BY wasted_usd DESC LIMIT 10"
```

## 5. Cache efficiency (money saved)

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py sql "SELECT project_name, SUM(total_cache_read) cache_read, SUM(total_input_tokens) input_tok, ROUND(SUM(total_cache_read) * 3.0 * 0.9 / 1e6, 2) as est_saved_usd, ROUND(SUM(total_cache_read)::FLOAT / NULLIF(SUM(total_input_tokens + total_cache_read), 0) * 100, 1) as cache_pct FROM sessions WHERE created_at >= current_date - INTERVAL '30 days' GROUP BY 1 HAVING SUM(total_cache_read) > 0 ORDER BY est_saved_usd DESC LIMIT 10"
```

## Presentation

Present as a dashboard with:

1. **Headline number**: estimated total spend this month
2. **Weekly trend**: is spending going up or down?
3. **Cost by project**: ranked table with cache efficiency
4. **Expensive sessions**: the outliers — what happened?
5. **Wasted tokens**: abandoned sessions that cost real money
6. **Savings from caching**: how much CLAUDE.md / project context saves
7. **Tip**: one actionable suggestion to reduce costs (e.g., "Project X has 0% cache hits — adding a CLAUDE.md could save ~$Y/month")
