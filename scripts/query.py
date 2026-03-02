#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2"]
# ///
"""claude-warehouse: CLI query interface for ~/.claude DuckDB store."""

import argparse
import sys
import time
from pathlib import Path

import duckdb

DB_PATH = Path.home() / ".claude" / "claude.duckdb"


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        print("Run sync.py first to create it.", file=sys.stderr)
        sys.exit(1)
    for attempt in range(5):
        try:
            return duckdb.connect(db_path, read_only=True)
        except duckdb.IOException:
            if attempt < 4:
                print("DB locked by sync, retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print("DB locked by sync. Try again in a moment.", file=sys.stderr)
                sys.exit(1)


def print_table(rows: list, headers: list[str], max_col: int = 60):
    if not rows:
        print("(no results)")
        return
    # Compute column widths
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = []
        for i, val in enumerate(row):
            s = str(val) if val is not None else ""
            if len(s) > max_col:
                s = s[:max_col - 1] + "…"
            sr.append(s)
            if i < len(widths):
                widths[i] = max(widths[i], len(s))
        str_rows.append(sr)

    # Header
    hdr = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(hdr)
    print("  ".join("─" * widths[i] for i in range(len(headers))))
    for sr in str_rows:
        print("  ".join(sr[i].ljust(widths[i]) if i < len(widths) else sr[i] for i in range(len(sr))))


def cmd_tokens(con: duckdb.DuckDBPyConnection, args):
    days = args.days or 7
    rows = con.execute(f"""
        SELECT
            project_name,
            COUNT(*) as sessions,
            SUM(total_input_tokens) as input_tok,
            SUM(total_output_tokens) as output_tok,
            SUM(total_cache_read) as cache_read,
            SUM(total_cache_write) as cache_write,
            SUM(total_input_tokens + total_output_tokens) as total
        FROM sessions
        WHERE created_at >= current_date - INTERVAL '{days} days'
        GROUP BY project_name
        ORDER BY total DESC
        LIMIT 30
    """).fetchall()
    print(f"Token usage by project (last {days} days)\n")

    def fmt(n):
        if n is None:
            return "0"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    formatted = [(r[0], str(r[1]), fmt(r[2]), fmt(r[3]), fmt(r[4]), fmt(r[5]), fmt(r[6])) for r in rows]
    print_table(formatted, ["Project", "Sessions", "Input", "Output", "CacheR", "CacheW", "Total"])

    # Totals
    if rows:
        total_in = sum(r[2] or 0 for r in rows)
        total_out = sum(r[3] or 0 for r in rows)
        total_all = sum(r[6] or 0 for r in rows)
        print(f"\nGrand total: {fmt(total_in)} in + {fmt(total_out)} out = {fmt(total_all)}")


def cmd_tools(con: duckdb.DuckDBPyConnection, args):
    days = args.days or 7
    rows = con.execute(f"""
        SELECT
            tool_name,
            COUNT(*) as calls,
            COUNT(DISTINCT session_id) as sessions
        FROM tool_calls
        WHERE timestamp >= current_date - INTERVAL '{days} days'
        GROUP BY tool_name
        ORDER BY calls DESC
        LIMIT 30
    """).fetchall()
    print(f"Most used tools (last {days} days)\n")
    print_table(rows, ["Tool", "Calls", "Sessions"])


def cmd_sessions(con: duckdb.DuckDBPyConnection, args):
    limit = args.limit or 20
    rows = con.execute(f"""
        SELECT
            strftime(created_at, '%m-%d %H:%M') as started,
            project_name,
            message_count as msgs,
            total_input_tokens + total_output_tokens as tokens,
            COALESCE(LEFT(first_prompt, 80), '') as prompt,
            session_id
        FROM sessions
        ORDER BY created_at DESC
        LIMIT {limit}
    """).fetchall()
    print(f"Recent sessions (last {limit})\n")

    def fmt(n):
        if n is None:
            return "0"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    formatted = [(r[0] or "", r[1] or "", str(r[2] or 0), fmt(r[3]), r[4] or "", r[5][:8]) for r in rows]
    print_table(formatted, ["Started", "Project", "Msgs", "Tokens", "Prompt", "ID"])


def cmd_search(con: duckdb.DuckDBPyConnection, args):
    q = args.query
    if not q:
        print("Usage: query.py search <query>", file=sys.stderr)
        sys.exit(1)

    # Search across messages
    rows = con.execute("""
        SELECT
            m.session_id,
            s.project_name,
            m.type,
            strftime(m.timestamp, '%m-%d %H:%M') as ts,
            LEFT(m.text_content, 200) as content
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.session_id
        WHERE m.text_content ILIKE '%' || ? || '%'
        ORDER BY m.timestamp DESC
        LIMIT 20
    """, [q]).fetchall()

    print(f"Messages matching '{q}'\n")
    formatted = [(r[3] or "", r[1] or "", r[2] or "", r[0][:8], r[4] or "") for r in rows]
    print_table(formatted, ["Time", "Project", "Type", "Session", "Content"])

    # Also search history
    hist = con.execute("""
        SELECT category, agent, strftime(timestamp, '%m-%d'), description
        FROM research_history
        WHERE content ILIKE '%' || ? || '%'
           OR description ILIKE '%' || ? || '%'
        ORDER BY timestamp DESC
        LIMIT 10
    """, [q, q]).fetchall()

    if hist:
        print(f"\nHistory matching '{q}'\n")
        print_table(hist, ["Category", "Agent", "Date", "Description"])


def cmd_projects(con: duckdb.DuckDBPyConnection, args):
    rows = con.execute("""
        SELECT
            project_name,
            COUNT(*) as sessions,
            MIN(created_at)::DATE as first_seen,
            MAX(created_at)::DATE as last_seen,
            SUM(message_count) as total_msgs,
            SUM(total_input_tokens + total_output_tokens) as total_tokens
        FROM sessions
        GROUP BY project_name
        ORDER BY last_seen DESC
        LIMIT 30
    """).fetchall()
    print("Project summary\n")

    def fmt(n):
        if n is None:
            return "0"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    formatted = [(r[0] or "", str(r[1]), str(r[2] or ""), str(r[3] or ""), str(r[4] or 0), fmt(r[5])) for r in rows]
    print_table(formatted, ["Project", "Sessions", "First", "Last", "Msgs", "Tokens"])


def cmd_size(con: duckdb.DuckDBPyConnection, args):
    db_file = Path(args.db)
    db_size = db_file.stat().st_size if db_file.exists() else 0

    counts = {}
    for table in ["sessions", "messages", "tool_calls", "hook_events", "todos", "debug_logs", "research_history"]:
        try:
            r = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = r[0]
        except Exception:
            counts[table] = 0

    print("Database statistics\n")
    print(f"  DB file: {db_size / 1024 / 1024:.1f} MB")
    print()
    for table, count in counts.items():
        print(f"  {table:.<25} {count:>10,} rows")

    # Sync state
    print("\nSync watermarks:\n")
    rows = con.execute("""
        SELECT source_name, last_run, files_synced, rows_synced
        FROM _sync_state ORDER BY source_name
    """).fetchall()
    print_table(rows, ["Source", "Last Run", "Files", "Rows"])


def cmd_hooks(con: duckdb.DuckDBPyConnection, args):
    days = args.days or 7
    rows = con.execute(f"""
        SELECT
            event_type,
            COUNT(*) as events,
            COUNT(DISTINCT session_id) as sessions,
            MIN(timestamp)::DATE as first,
            MAX(timestamp)::DATE as last
        FROM hook_events
        WHERE timestamp >= current_date - INTERVAL '{days} days'
        GROUP BY event_type
        ORDER BY events DESC
    """).fetchall()
    print(f"Hook events (last {days} days)\n")
    print_table(rows, ["Event", "Count", "Sessions", "First", "Last"])


def cmd_sql(con: duckdb.DuckDBPyConnection, args):
    query = args.query
    if not query:
        print("Usage: query.py sql \"SELECT ...\"", file=sys.stderr)
        sys.exit(1)
    try:
        result = con.execute(query)
        cols = [desc[0] for desc in result.description]
        rows = result.fetchall()
        print_table(rows, cols)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="claude-warehouse query", prog="cw")
    parser.add_argument("--db", default=str(DB_PATH))

    sub = parser.add_subparsers(dest="command")

    p_tokens = sub.add_parser("tokens", help="Token usage by project")
    p_tokens.add_argument("--days", "-d", type=int, default=7)

    p_tools = sub.add_parser("tools", help="Most used tools")
    p_tools.add_argument("--days", "-d", type=int, default=7)

    p_sessions = sub.add_parser("sessions", help="Recent sessions")
    p_sessions.add_argument("--limit", "-n", type=int, default=20)

    p_search = sub.add_parser("search", help="Full-text search")
    p_search.add_argument("query", nargs="?")

    p_projects = sub.add_parser("projects", help="Project summary")

    p_size = sub.add_parser("size", help="DB size and row counts")

    p_hooks = sub.add_parser("hooks", help="Hook event summary")
    p_hooks.add_argument("--days", "-d", type=int, default=7)

    p_sql = sub.add_parser("sql", help="Run raw SQL")
    p_sql.add_argument("query", nargs="?")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    con = connect(args.db)

    cmds = {
        "tokens": cmd_tokens,
        "tools": cmd_tools,
        "sessions": cmd_sessions,
        "search": cmd_search,
        "projects": cmd_projects,
        "size": cmd_size,
        "hooks": cmd_hooks,
        "sql": cmd_sql,
    }

    cmds[args.command](con, args)
    con.close()


if __name__ == "__main__":
    main()
