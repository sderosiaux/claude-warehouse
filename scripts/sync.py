#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2"]
# ///
"""claude-warehouse: incremental ETL from ~/.claude into DuckDB."""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

CLAUDE_DIR = Path.home() / ".claude"
DB_PATH = CLAUDE_DIR / "claude.duckdb"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(con: duckdb.DuckDBPyConnection):
    con.execute(SCHEMA_PATH.read_text())


def get_watermark(con: duckdb.DuckDBPyConnection, source: str) -> float:
    r = con.execute(
        "SELECT last_mtime FROM _sync_state WHERE source_name = ?", [source]
    ).fetchone()
    return r[0] if r else 0.0


def set_watermark(con: duckdb.DuckDBPyConnection, source: str, mtime: float, files: int, rows: int):
    con.execute("""
        INSERT INTO _sync_state (source_name, last_mtime, last_run, files_synced, rows_synced)
        VALUES (?, ?, current_timestamp, ?, ?)
        ON CONFLICT (source_name)
        DO UPDATE SET last_mtime = excluded.last_mtime,
                      last_run = excluded.last_run,
                      files_synced = _sync_state.files_synced + excluded.files_synced,
                      rows_synced = _sync_state.rows_synced + excluded.rows_synced
    """, [source, mtime, files, rows])


def newer_files(directory: Path, watermark: float, suffix: str = "", recurse: bool = False) -> list[Path]:
    if not directory.exists():
        return []
    files = []
    glob_pat = f"**/*{suffix}" if recurse else f"*{suffix}"
    for p in directory.glob(glob_pat):
        if p.is_file():
            mtime = p.stat().st_mtime
            if mtime > watermark:
                files.append((mtime, p))
    files.sort(key=lambda x: x[0])
    return [p for _, p in files]


def truncate(s: str | None, maxlen: int = 500) -> str | None:
    if s is None:
        return None
    return s[:maxlen] if len(s) > maxlen else s


# ---------------------------------------------------------------------------
# Sessions + Messages + Tool Calls (main + subagent JSONL)
# ---------------------------------------------------------------------------

def extract_text_content(content_blocks: list) -> str | None:
    texts = []
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
        elif isinstance(block, str):
            texts.append(block)
    return "\n".join(texts) if texts else None


def extract_first_prompt(content) -> str | None:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return truncate(block.get("text", ""), 1000)
            elif isinstance(block, str):
                return truncate(block, 1000)
    elif isinstance(content, str):
        return truncate(content, 1000)
    return None


def _ingest_jsonl(con: duckdb.DuckDBPyConnection, fp: Path, is_subagent: bool = False,
                  parent_session_id: str | None = None):
    """Parse a single JSONL file into sessions/messages/tool_calls. Returns (session_id, msg_count) or None."""
    sid = fp.stem
    project_dir = fp.parent.name
    # For subagents, use filename as unique key since they share parent's sessionId
    override_sid = None
    if is_subagent:
        project_dir = fp.parent.parent.parent.name
        override_sid = fp.stem  # e.g. "agent-a512e64"

    messages = []
    tool_calls_batch = []
    first_user_prompt = None
    session_meta = {}
    token_totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    tools_seen = set()
    models_seen = set()

    try:
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = rec.get("type", "")
                if msg_type == "file-history-snapshot":
                    continue

                uuid = rec.get("uuid", "")
                if not uuid:
                    continue

                if not session_meta and rec.get("sessionId"):
                    session_meta = {
                        "session_id": rec.get("sessionId", sid),
                        "cwd": rec.get("cwd"),
                        "version": rec.get("version"),
                        "git_branch": rec.get("gitBranch"),
                    }

                ts_str = rec.get("timestamp")
                ts = None
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                is_sidechain = rec.get("isSidechain", False)
                msg_payload = rec.get("message", {})
                role = msg_payload.get("role")
                model = msg_payload.get("model")
                stop_reason = msg_payload.get("stop_reason")
                usage = msg_payload.get("usage", {})
                content = msg_payload.get("content", [])

                if model:
                    models_seen.add(model)

                inp_tok = usage.get("input_tokens", 0) or 0
                out_tok = usage.get("output_tokens", 0) or 0
                cache_r = usage.get("cache_read_input_tokens", 0) or 0
                cache_w = usage.get("cache_creation_input_tokens", 0) or 0

                token_totals["input"] += inp_tok
                token_totals["output"] += out_tok
                token_totals["cache_read"] += cache_r
                token_totals["cache_write"] += cache_w

                content_types = []
                tool_name = None
                tool_input_summary = None
                text_content = None

                if isinstance(content, list):
                    for i, block in enumerate(content):
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        if bt not in content_types:
                            content_types.append(bt)

                        if bt == "tool_use":
                            tn = block.get("name", "")
                            tools_seen.add(tn)
                            if not tool_name:
                                tool_name = tn
                                ti = block.get("input", {})
                                tool_input_summary = truncate(json.dumps(ti, default=str), 300)

                            tool_calls_batch.append((
                                session_meta.get("session_id", sid),
                                uuid, tn,
                                truncate(json.dumps(block.get("input", {}), default=str), 500),
                                ts.isoformat() if ts else None, i,
                            ))

                    text_content = extract_text_content(content)

                if msg_type == "user" and first_user_prompt is None and not is_sidechain:
                    first_user_prompt = extract_first_prompt(content)

                messages.append((
                    session_meta.get("session_id", sid), uuid,
                    rec.get("parentUuid"), msg_type,
                    ts.isoformat() if ts else None, is_sidechain,
                    role, model, stop_reason,
                    inp_tok, out_tok, cache_r, cache_w,
                    json.dumps(content_types) if content_types else None,
                    tool_name, tool_input_summary,
                    truncate(text_content, 2000),
                ))
    except Exception:
        return None

    if not messages:
        return None

    timestamps = [m[4] for m in messages if m[4]]
    created = min(timestamps) if timestamps else None
    modified = max(timestamps) if timestamps else None
    s_id = override_sid or session_meta.get("session_id", sid)

    # Rewrite session_id in messages and tool_calls to use s_id
    if override_sid:
        messages = [(s_id, *m[1:]) for m in messages]
        tool_calls_batch = [(s_id, *t[1:]) for t in tool_calls_batch]

    con.execute("""
        INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (session_id) DO UPDATE SET
            modified_at = excluded.modified_at,
            message_count = excluded.message_count,
            total_input_tokens = excluded.total_input_tokens,
            total_output_tokens = excluded.total_output_tokens,
            total_cache_read = excluded.total_cache_read,
            total_cache_write = excluded.total_cache_write,
            tools_used = excluded.tools_used,
            models_used = excluded.models_used,
            first_prompt = excluded.first_prompt
    """, [
        s_id, str(fp.parent), project_dir,
        session_meta.get("git_branch"),
        session_meta.get("version"),
        session_meta.get("cwd"),
        created, modified, len(messages),
        token_totals["input"], token_totals["output"],
        token_totals["cache_read"], token_totals["cache_write"],
        json.dumps(sorted(tools_seen)),
        json.dumps(sorted(models_seen)),
        first_user_prompt, str(fp),
        is_subagent, parent_session_id,
    ])

    con.execute("DELETE FROM messages WHERE session_id = ?", [s_id])
    con.execute("DELETE FROM tool_calls WHERE session_id = ?", [s_id])

    # Deduplicate: JSONL files can contain duplicate entries (retries, replayed events).
    # Keep last occurrence per key since it has the most up-to-date data.
    if messages:
        deduped = {(m[0], m[1]): m for m in messages}  # key: (session_id, uuid)
        con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(deduped.values()))
    if tool_calls_batch:
        deduped_tc = {(t[0], t[1], t[5]): t for t in tool_calls_batch}  # key: (session_id, message_uuid, idx)
        con.executemany("INSERT INTO tool_calls VALUES (?,?,?,?,?,?)", list(deduped_tc.values()))

    return s_id, len(messages)


def _scan_jsonl_files(projects_dir: Path, session_wm: float, subagent_wm: float):
    """Single rglob scan, partitioned into sessions and subagents with cached mtimes."""
    sessions = []
    subagents = []
    for p in projects_dir.rglob("*.jsonl"):
        if not p.is_file():
            continue
        mtime = p.stat().st_mtime
        is_sub = "/subagents/" in str(p)
        if is_sub and mtime > subagent_wm:
            subagents.append((mtime, p))
        elif not is_sub and mtime > session_wm:
            sessions.append((mtime, p))
    sessions.sort(key=lambda x: x[0])
    subagents.sort(key=lambda x: x[0])
    return sessions, subagents


def sync_sessions(con: duckdb.DuckDBPyConnection, session_files: list[tuple[float, Path]],
                  session_id_filter: str | None = None, verbose: bool = False):
    wm = get_watermark(con, "sessions")

    if session_id_filter:
        projects_dir = CLAUDE_DIR / "projects"
        candidates = list(projects_dir.rglob(f"{session_id_filter}.jsonl"))
        files = [(f.stat().st_mtime, f) for f in candidates if "/subagents/" not in str(f)]
        if not files:
            if verbose:
                print(f"  Session {session_id_filter} not found")
            return
    else:
        files = session_files

    if verbose:
        print(f"  Sessions: {len(files)} files to process")

    max_mtime = wm
    total_rows = 0

    for mtime, fp in files:
        max_mtime = max(max_mtime, mtime)
        result = _ingest_jsonl(con, fp, is_subagent=False)
        if result:
            total_rows += result[1]

    set_watermark(con, "sessions", max_mtime, len(files), total_rows)


def sync_subagents(con: duckdb.DuckDBPyConnection, subagent_files: list[tuple[float, Path]],
                   verbose: bool = False):
    if verbose:
        print(f"  Subagents: {len(subagent_files)} files to process")

    wm = get_watermark(con, "subagents")
    max_mtime = wm
    total_rows = 0

    for mtime, fp in subagent_files:
        max_mtime = max(max_mtime, mtime)
        parent_dir = fp.parent.parent.name
        parent_sid = parent_dir if parent_dir != "subagents" else None

        result = _ingest_jsonl(con, fp, is_subagent=True, parent_session_id=parent_sid)
        if result:
            total_rows += result[1]

    set_watermark(con, "subagents", max_mtime, len(subagent_files), total_rows)


# ---------------------------------------------------------------------------
# Deleted Sessions (from sessions-index.json)
# ---------------------------------------------------------------------------

def sync_deleted_sessions(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    wm = get_watermark(con, "deleted_sessions")
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return

    index_files = list(projects_dir.rglob("sessions-index.json"))
    index_files = [f for f in index_files if f.stat().st_mtime > wm]

    if verbose:
        print(f"  Deleted sessions: {len(index_files)} index files to process")

    max_mtime = wm
    total_rows = 0

    for idx_file in index_files:
        max_mtime = max(max_mtime, idx_file.stat().st_mtime)
        project_name = idx_file.parent.name

        try:
            data = json.loads(idx_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        project_path = data.get("originalPath", "")

        for entry in data.get("entries", []):
            sid = entry.get("sessionId", "")
            if not sid:
                continue
            # Skip if the JSONL file still exists (already in sessions table)
            fp = entry.get("fullPath", "")
            if fp and Path(fp).exists():
                continue

            created = entry.get("created")
            modified = entry.get("modified")

            con.execute("""
                INSERT INTO deleted_sessions VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT (session_id) DO NOTHING
            """, [
                sid, project_path, project_name,
                entry.get("gitBranch"),
                created, modified,
                entry.get("messageCount", 0),
                truncate(entry.get("firstPrompt"), 1000),
                truncate(entry.get("summary"), 500),
            ])
            total_rows += 1

    set_watermark(con, "deleted_sessions", max_mtime, len(index_files), total_rows)


# ---------------------------------------------------------------------------
# Hook Events
# ---------------------------------------------------------------------------

LOG_FILES = {
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "sessionstart": "SessionStart",
    "sessionend": "SessionEnd",
    "precompact": "PreCompact",
    "notification": "Notification",
    "userpromptsubmit": "UserPromptSubmit",
    "subagentstop": "SubagentStop",
    "adversarial-session-start": "AdversarialSessionStart",
    "adversarial-spawn": "AdversarialSpawn",
    "adversarial-stop-hook": "AdversarialStop",
    "ensure-stop": "EnsureStop",
    "retry-until-stop": "RetryUntilStop",
}

# Pattern: "2026-01-31 10:28:50 {json...}" or "2026-01-31 10:28:50 non-json text"
LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+)$")


def sync_hook_events(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    logs_dir = CLAUDE_DIR / "logs"
    if not logs_dir.exists():
        return

    total_rows = 0
    for log_stem, event_type in LOG_FILES.items():
        log_file = logs_dir / f"{log_stem}.log"
        if not log_file.exists():
            continue

        wm_key = f"hooks_{log_stem}"
        wm = get_watermark(con, wm_key)
        current_size = log_file.stat().st_size

        # Use file size as watermark — skip if file hasn't grown
        if current_size <= wm:
            continue

        # Get line count to continue numbering
        existing = con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM hook_events WHERE file_path = ?", [str(log_file)]
        ).fetchone()[0]

        rows = []
        line_idx = existing
        with open(log_file, errors="replace") as f:
            # Seek past already-processed bytes
            if wm > 0:
                f.seek(int(wm))
            for line in f:
                line = line.strip()
                if not line:
                    continue

                m = LOG_LINE_RE.match(line)
                if not m:
                    continue

                line_idx += 1
                ts_str, payload = m.group(1), m.group(2)
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                sid = None
                cwd = None
                tool_name = None
                tool_input = None

                try:
                    data = json.loads(payload)
                    sid = data.get("session_id")
                    cwd = data.get("cwd")
                    tool_name = data.get("tool_name")
                    tool_input = data.get("tool_input")
                except json.JSONDecodeError:
                    pass

                rows.append((
                    line_idx, event_type, sid,
                    ts.isoformat(), cwd, tool_name,
                    truncate(json.dumps(tool_input, default=str), 500) if tool_input else None,
                    str(log_file),
                ))

        if rows:
            con.executemany("""
                INSERT INTO hook_events VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT DO NOTHING
            """, rows)
            total_rows += len(rows)

        if verbose:
            print(f"  Hooks [{event_type}]: +{len(rows)} events")

        set_watermark(con, wm_key, float(current_size), 1, len(rows))


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------

def sync_todos(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    wm = get_watermark(con, "todos")
    todos_dir = CLAUDE_DIR / "todos"
    files = newer_files(todos_dir, wm, ".json")

    if verbose:
        print(f"  Todos: {len(files)} files to process")

    max_mtime = wm
    total_rows = 0

    for fp in files:
        max_mtime = max(max_mtime, fp.stat().st_mtime)
        fname = fp.name
        sid = fname.split("-agent-")[0] if "-agent-" in fname else fp.stem

        try:
            data = json.loads(fp.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, list):
            continue

        con.execute("DELETE FROM todos WHERE file_name = ?", [fname])

        rows = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            rows.append((
                fname, i, sid,
                item.get("content"),
                item.get("status"),
                item.get("activeForm"),
            ))

        if rows:
            con.executemany("INSERT INTO todos VALUES (?,?,?,?,?,?)", rows)
            total_rows += len(rows)

    set_watermark(con, "todos", max_mtime, len(files), total_rows)


# ---------------------------------------------------------------------------
# Debug Logs
# ---------------------------------------------------------------------------

def sync_debug(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    wm = get_watermark(con, "debug")
    debug_dir = CLAUDE_DIR / "debug"
    files = newer_files(debug_dir, wm, ".txt")

    if verbose:
        print(f"  Debug: {len(files)} files to process")

    max_mtime = wm
    total_rows = 0

    for fp in files:
        max_mtime = max(max_mtime, fp.stat().st_mtime)
        sid = fp.stem

        try:
            stat = fp.stat()
            first_line = ""
            line_count = 0
            ts = None
            with open(fp, errors="replace") as f:
                for i, line in enumerate(f):
                    line_count += 1
                    if i == 0:
                        first_line = line.strip()[:500]
                        ts_match = re.match(r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)", first_line)
                        if ts_match:
                            try:
                                ts = datetime.fromisoformat(ts_match.group(1).replace("Z", "+00:00"))
                            except ValueError:
                                pass
        except OSError:
            continue

        con.execute("""
            INSERT INTO debug_logs VALUES (?,?,?,?,?,?)
            ON CONFLICT (file_path) DO UPDATE SET
                line_count = excluded.line_count,
                size_bytes = excluded.size_bytes
        """, [str(fp), sid, ts.isoformat() if ts else None, first_line, line_count, stat.st_size])
        total_rows += 1

    set_watermark(con, "debug", max_mtime, len(files), total_rows)


# ---------------------------------------------------------------------------
# Research History
# ---------------------------------------------------------------------------

HISTORY_RE = re.compile(r"^(\d{8}T\d{6})_(\w+)_(\w+)_(.+)\.md$")


def sync_history(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    wm = get_watermark(con, "history")
    history_dir = CLAUDE_DIR / "history"
    files = newer_files(history_dir, wm, ".md", recurse=True)

    if verbose:
        print(f"  History: {len(files)} files to process")

    max_mtime = wm
    total_rows = 0

    for fp in files:
        max_mtime = max(max_mtime, fp.stat().st_mtime)
        category = fp.parent.name if fp.parent != history_dir else "uncategorized"

        m = HISTORY_RE.match(fp.name)
        agent = None
        ts = None
        description = fp.stem

        if m:
            ts_str, _, agent, description = m.groups()
            try:
                ts = datetime.strptime(ts_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
            description = description.replace("-", " ")[:200]

        try:
            text = fp.read_text(errors="replace")
            for line in text.split("\n")[:10]:
                if line.startswith("agent:"):
                    agent = agent or line.split(":", 1)[1].strip()
                if line.startswith("timestamp:"):
                    if not ts:
                        try:
                            ts = datetime.fromisoformat(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
        except OSError:
            text = ""

        con.execute("""
            INSERT INTO research_history VALUES (?,?,?,?,?,?)
            ON CONFLICT (file_path) DO UPDATE SET content = excluded.content
        """, [
            str(fp), category, agent,
            ts.isoformat() if ts else None,
            description, truncate(text, 10000),
        ])
        total_rows += 1

    set_watermark(con, "history", max_mtime, len(files), total_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="claude-warehouse sync")
    parser.add_argument("--session", help="Sync only this session ID")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--full", action="store_true", help="Reset watermarks and re-sync everything")
    parser.add_argument("--compact", action="store_true", help="Vacuum and checkpoint DB after sync")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")
    args = parser.parse_args()

    t0 = time.time()
    con = duckdb.connect(args.db)
    init_db(con)

    if args.full:
        con.execute("DELETE FROM _sync_state")
        if args.verbose:
            print("Reset all watermarks for full re-sync")

    if args.verbose:
        print(f"Syncing to {args.db}")

    projects_dir = CLAUDE_DIR / "projects"
    session_files, subagent_files = [], []
    if projects_dir.exists():
        session_wm = get_watermark(con, "sessions")
        subagent_wm = get_watermark(con, "subagents")
        session_files, subagent_files = _scan_jsonl_files(projects_dir, session_wm, subagent_wm)

    def _timed(name, fn):
        t = time.time()
        fn()
        if args.verbose:
            print(f"    [{time.time() - t:.2f}s]")

    _timed("sessions", lambda: sync_sessions(con, session_files, session_id_filter=args.session, verbose=args.verbose))
    _timed("subagents", lambda: sync_subagents(con, subagent_files, verbose=args.verbose))
    _timed("deleted", lambda: sync_deleted_sessions(con, verbose=args.verbose))
    _timed("hooks", lambda: sync_hook_events(con, verbose=args.verbose))
    _timed("todos", lambda: sync_todos(con, verbose=args.verbose))
    _timed("debug", lambda: sync_debug(con, verbose=args.verbose))
    _timed("history", lambda: sync_history(con, verbose=args.verbose))

    if args.compact:
        if args.verbose:
            print("  Compacting database...")
        con.execute("CHECKPOINT")
        con.execute("VACUUM")

    con.close()

    elapsed = time.time() - t0
    if args.verbose:
        print(f"Done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
