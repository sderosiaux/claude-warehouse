#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2"]
# ///
"""claude-warehouse: HTTP dashboard server on port 3141."""

import json
import socket
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import duckdb

DB_PATH = Path.home() / ".claude" / "claude.duckdb"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PORT = 3141


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def connect() -> duckdb.DuckDBPyConnection:
    for attempt in range(5):
        try:
            return duckdb.connect(str(DB_PATH), read_only=True)
        except (duckdb.IOException, duckdb.OperationalError):
            if attempt < 4:
                time.sleep(1)
            else:
                raise


def query(sql: str) -> list[dict]:
    con = connect()
    try:
        result = con.execute(sql)
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    finally:
        con.close()


def safe_json(obj):
    """JSON serializer that handles dates, Decimals, etc."""
    import datetime
    import decimal
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Not serializable: {type(obj)}")


# ── API handlers ──

def api_overview() -> dict:
    rows = query("""
        SELECT 'last_30d' as period, COUNT(*) sessions, SUM(message_count) messages,
            SUM(total_input_tokens + total_output_tokens) total_tokens,
            ROUND(AVG(message_count), 1) avg_msgs,
            COUNT(DISTINCT project_name) projects,
            ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2) est_cost_usd
        FROM sessions WHERE created_at >= current_date - INTERVAL '30 days'
        UNION ALL
        SELECT 'prev_30d', COUNT(*), SUM(message_count),
            SUM(total_input_tokens + total_output_tokens),
            ROUND(AVG(message_count), 1), COUNT(DISTINCT project_name),
            ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2)
        FROM sessions WHERE created_at >= current_date - INTERVAL '60 days'
            AND created_at < current_date - INTERVAL '30 days'
    """)
    sync = query("SELECT MAX(last_run) as last_sync FROM _sync_state")
    return {"periods": rows, "last_sync": sync[0]["last_sync"] if sync else None}


def api_costs() -> list[dict]:
    return query("""
        SELECT project_name, COUNT(*) sessions,
            SUM(total_input_tokens) input_tok, SUM(total_output_tokens) output_tok,
            SUM(total_cache_read) cache_read,
            ROUND(SUM(total_cache_read)::FLOAT / NULLIF(SUM(total_input_tokens + total_cache_read), 0) * 100, 1) cache_hit_pct,
            ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2) est_cost_usd
        FROM sessions WHERE created_at >= current_date - INTERVAL '30 days'
        GROUP BY 1 ORDER BY est_cost_usd DESC LIMIT 10
    """)


def api_tools() -> list[dict]:
    return query("""
        SELECT tool_name, COUNT(*) calls,
            ROUND(COUNT(*)::FLOAT / SUM(COUNT(*)) OVER () * 100, 1) pct
        FROM tool_calls WHERE timestamp >= current_date - INTERVAL '30 days'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """)


def api_sessions() -> list[dict]:
    return query("""
        SELECT strftime(created_at, '%Y-%m-%d %H:%M') as started,
            project_name, message_count msgs,
            total_input_tokens + total_output_tokens as tokens,
            ROUND(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6, 2) est_cost_usd,
            COALESCE(LEFT(first_prompt, 120), '') as prompt,
            LEFT(session_id, 8) as id
        FROM sessions ORDER BY created_at DESC LIMIT 30
    """)


def api_trends() -> dict:
    daily = query("""
        SELECT created_at::DATE as day, COUNT(*) sessions, SUM(message_count) msgs
        FROM sessions WHERE created_at >= current_date - INTERVAL '14 days'
        GROUP BY 1 ORDER BY 1
    """)
    weekly = query("""
        SELECT DATE_TRUNC('week', timestamp)::DATE as week,
            COUNT(*) FILTER (WHERE tool_name IN ('Edit', 'MultiEdit', 'Write')) as writes,
            COUNT(*) FILTER (WHERE tool_name = 'Read') as reads,
            ROUND(COUNT(*) FILTER (WHERE tool_name IN ('Edit', 'MultiEdit', 'Write'))::FLOAT /
                NULLIF(COUNT(*) FILTER (WHERE tool_name = 'Read'), 0), 2) as write_read_ratio
        FROM tool_calls GROUP BY 1 ORDER BY 1 DESC LIMIT 8
    """)
    weekly.reverse()
    return {"daily": daily, "weekly": weekly}


def api_efficiency() -> dict:
    buckets = query("""
        SELECT CASE WHEN message_count <= 3 THEN 'abandoned'
            WHEN message_count <= 10 THEN 'short'
            WHEN message_count <= 30 THEN 'medium'
            ELSE 'long' END as bucket,
            COUNT(*) sessions,
            ROUND(AVG(total_input_tokens + total_output_tokens)) avg_tokens,
            ROUND(AVG(message_count), 1) avg_msgs
        FROM sessions WHERE created_at >= current_date - INTERVAL '30 days'
        GROUP BY 1 ORDER BY MIN(message_count)
    """)
    prompt_quality = query("""
        SELECT CASE WHEN LENGTH(first_prompt) < 50 THEN 'short'
            WHEN LENGTH(first_prompt) < 200 THEN 'medium'
            ELSE 'detailed' END as prompt_length,
            COUNT(*) sessions, ROUND(AVG(message_count), 1) avg_msgs,
            ROUND(AVG(total_input_tokens + total_output_tokens)) avg_tokens,
            ROUND(AVG(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6), 2) avg_cost_usd
        FROM sessions WHERE created_at >= current_date - INTERVAL '30 days'
            AND first_prompt IS NOT NULL
        GROUP BY 1 ORDER BY MIN(LENGTH(first_prompt))
    """)
    return {"buckets": buckets, "prompt_quality": prompt_quality}


def api_wrapped() -> dict:
    streak = query("""
        WITH days AS (SELECT DISTINCT created_at::DATE as d FROM sessions),
        streaks AS (SELECT d, d - ROW_NUMBER() OVER (ORDER BY d) * INTERVAL '1 day' as grp FROM days)
        SELECT COUNT(*) as streak_days, MIN(d)::DATE as from_date, MAX(d)::DATE as to_date
        FROM streaks GROUP BY grp ORDER BY streak_days DESC LIMIT 1
    """)
    peak = query("""
        SELECT EXTRACT(HOUR FROM created_at)::INT as hour, COUNT(*) sessions
        FROM sessions GROUP BY 1 ORDER BY 2 DESC LIMIT 3
    """)
    marathon = query("""
        SELECT project_name, created_at::DATE as date, message_count msgs,
            total_input_tokens + total_output_tokens as tokens,
            ROUND(total_input_tokens * 3.0 / 1e6 + total_output_tokens * 15.0 / 1e6, 2) est_cost_usd,
            LEFT(first_prompt, 100) prompt
        FROM sessions ORDER BY message_count DESC LIMIT 1
    """)
    alltime = query("""
        SELECT COUNT(*) total_sessions, SUM(message_count) total_messages,
            SUM(total_input_tokens + total_output_tokens) total_tokens,
            COUNT(DISTINCT project_name) total_projects,
            MIN(created_at)::DATE first_session, MAX(created_at)::DATE latest_session,
            ROUND(SUM(total_input_tokens) * 3.0 / 1e6 + SUM(total_output_tokens) * 15.0 / 1e6, 2) total_cost_usd
        FROM sessions
    """)
    # Determine "developer type" from top tool
    top_tool = query("""
        SELECT tool_name, COUNT(*) c FROM tool_calls
        GROUP BY 1 ORDER BY 2 DESC LIMIT 1
    """)
    type_map = {
        "Edit": "The Surgeon", "Read": "The Scholar", "Bash": "The Hacker",
        "Write": "The Architect", "Grep": "The Detective", "Glob": "The Explorer",
        "Task": "The Orchestrator", "WebSearch": "The Researcher",
    }
    tool_name = top_tool[0]["tool_name"] if top_tool else "unknown"
    dev_type = type_map.get(tool_name, "The Polyglot")

    return {
        "streak": streak[0] if streak else None,
        "peak_hours": peak,
        "marathon": marathon[0] if marathon else None,
        "alltime": alltime[0] if alltime else None,
        "dev_type": dev_type,
        "top_tool": tool_name,
    }


# ── HTTP handler ──

ROUTES = {
    "/api/overview": api_overview,
    "/api/costs": api_costs,
    "/api/tools": api_tools,
    "/api/sessions": api_sessions,
    "/api/trends": api_trends,
    "/api/efficiency": api_efficiency,
    "/api/wrapped": api_wrapped,
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ROUTES:
            try:
                data = ROUTES[path]()
                body = json.dumps(data, default=safe_json).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.write(body)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write(body)
            return

        # Serve / as /index.html
        if path == "/":
            self.path = "/index.html"

        super().do_GET()

    def write(self, data: bytes):
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def log_message(self, format, *args):
        pass  # silent


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        print("Run sync.py first.", file=sys.stderr)
        sys.exit(1)

    if not STATIC_DIR.exists():
        print(f"Static dir not found: {STATIC_DIR}", file=sys.stderr)
        sys.exit(1)

    if port_in_use(PORT):
        # Already running — idempotent exit
        sys.exit(0)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
