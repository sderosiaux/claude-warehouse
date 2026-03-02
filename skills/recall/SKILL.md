---
description: Search across all past Claude Code sessions. Use when you need to recall previous work, find solutions to problems you've solved before, or retrieve context from past conversations. Powered by DuckDB full-text search over session history.
---

# Recall — Cross-Session Memory

Search across all past Claude Code sessions stored in the local DuckDB warehouse.

## When to use

- "Have I worked on this before?"
- "How did I solve X last time?"
- "What did I do in project Y?"
- "Find all sessions where we discussed Z"

## How to search

Run the search script. It queries messages, session metadata, and research history.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py search "$ARGUMENTS"
```

## Interpreting results

The search returns:
- **Messages**: matching text from past conversations (with session ID, project, timestamp)
- **Research history**: matching entries from research/review artifacts

Use the session ID to dig deeper with `/claude-warehouse:query` if needed.

## Tips

- Search for error messages, library names, patterns, concepts
- Use short, specific terms for best results
- Combine with `/claude-warehouse:query` for complex lookups (e.g., "all sessions in project X that used tool Y")
