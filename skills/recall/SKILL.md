---
description: Search across all past Claude Code sessions. Use when you need to recall previous work, find solutions to problems you've solved before, or retrieve context from past conversations. Powered by DuckDB keyword search and semantic vector search over session history.
---

# Recall — Cross-Session Memory

Search across all past Claude Code sessions stored in the local DuckDB warehouse.

## When to use

- "Have I worked on this before?"
- "How did I solve X last time?"
- "What did I do in project Y?"
- "Find all sessions where we discussed Z"

## How to search

Always run BOTH searches — they complement each other.

### 1. Keyword search (exact substring match)
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/query.py search "$ARGUMENTS"
```

### 2. Semantic search (meaning-based, finds related concepts)
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/vsearch.py "$ARGUMENTS"
```
Finds results even when exact words don't match. Supports filters: `--project X`, `--days N`, `--type message|session|research`, `--limit N`.

## Interpreting results

**Keyword** returns exact substring matches — high precision, low recall.
**Semantic** returns conceptually similar content — lower precision, high recall. Score > 0.7 = strong match, 0.5-0.7 = related.

Combine both to get a complete picture. Use session IDs to dig deeper with `/claude-warehouse:query`.

## Tips

- Search for error messages, library names, patterns, concepts
- Use short, specific terms for keyword search
- Use natural language descriptions for semantic search
- Combine with `/claude-warehouse:query` for complex lookups (e.g., "all sessions in project X that used tool Y")
