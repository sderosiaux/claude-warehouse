#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2", "sentence-transformers>=3.0", "torch"]
# ///
"""claude-warehouse: semantic vector search across past sessions."""

import argparse
import sys
import time
from pathlib import Path

import duckdb

DB_PATH = Path.home() / ".claude" / "claude.duckdb"
MODEL_NAME = "all-MiniLM-L6-v2"


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    for attempt in range(5):
        try:
            con = duckdb.connect(db_path, read_only=True)
            con.execute("LOAD vss")
            return con
        except duckdb.IOException:
            if attempt < 4:
                time.sleep(2)
            else:
                print("DB locked. Try again in a moment.", file=sys.stderr)
                sys.exit(1)


def print_table(rows: list, headers: list[str], max_col: int = 80):
    if not rows:
        print("(no results)")
        return
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
    hdr = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(hdr)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for sr in str_rows:
        print("  ".join(sr[i].ljust(widths[i]) if i < len(widths) else sr[i] for i in range(len(sr))))


def search(con: duckdb.DuckDBPyConnection, query_embedding: list[float],
           source_type: str | None, project: str | None,
           days: int | None, limit: int):
    # Build query parts
    filters = []
    params = []

    if source_type:
        filters.append("e.source_type = ?")
        params.append(source_type)

    where = ("AND " + " AND ".join(filters)) if filters else ""

    # Base vector search
    rows = con.execute(f"""
        SELECT
            e.source_type,
            e.source_id,
            e.chunk_idx,
            e.text_preview,
            array_cosine_similarity(e.embedding, ?::FLOAT[384]) as score
        FROM embeddings e
        WHERE e.embedding IS NOT NULL {where}
        ORDER BY array_cosine_distance(e.embedding, ?::FLOAT[384])
        LIMIT ?
    """, [query_embedding, *params, query_embedding, limit * 3]).fetchall()

    # Enrich with metadata
    enriched = []
    for source_type_r, source_id, chunk_idx, preview, score in rows:
        meta = enrich(con, source_type_r, source_id)
        if project and meta.get("project", "").lower() != project.lower():
            continue
        if days and meta.get("created_at"):
            age_check = con.execute(
                "SELECT ? >= current_date - INTERVAL ? DAY",
                [meta["created_at"], days],
            ).fetchone()
            if age_check and not age_check[0]:
                continue
        enriched.append({
            "score": f"{score:.3f}",
            "type": source_type_r,
            "project": meta.get("project", ""),
            "date": meta.get("date", ""),
            "preview": preview or "",
            "session": meta.get("session_id", "")[:8],
        })
        if len(enriched) >= limit:
            break

    return enriched


def enrich(con: duckdb.DuckDBPyConnection, source_type: str, source_id: str) -> dict:
    if source_type == "message":
        parts = source_id.split(":", 1)
        if len(parts) == 2:
            sid = parts[0]
            row = con.execute("""
                SELECT s.project_name, strftime(m.timestamp, '%m-%d %H:%M'), s.session_id, m.timestamp
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.session_id
                WHERE m.session_id = ? AND m.uuid = ?
                LIMIT 1
            """, [sid, parts[1]]).fetchone()
            if row:
                return {"project": row[0] or "", "date": row[1] or "", "session_id": row[2] or "", "created_at": row[3]}
    elif source_type == "session":
        row = con.execute("""
            SELECT project_name, strftime(created_at, '%m-%d %H:%M'), session_id, created_at
            FROM sessions WHERE session_id = ?
        """, [source_id]).fetchone()
        if row:
            return {"project": row[0] or "", "date": row[1] or "", "session_id": row[2] or "", "created_at": row[3]}
    elif source_type == "research":
        row = con.execute("""
            SELECT category, strftime(timestamp, '%m-%d %H:%M'), timestamp
            FROM research_history WHERE file_path = ?
        """, [source_id]).fetchone()
        if row:
            return {"project": row[0] or "", "date": row[1] or "", "session_id": "", "created_at": row[2]}
    return {}


def main():
    parser = argparse.ArgumentParser(description="claude-warehouse vector search", prog="vsearch")
    parser.add_argument("query", help="Search query (semantic)")
    parser.add_argument("--project", "-p", help="Filter by project name")
    parser.add_argument("--days", "-d", type=int, help="Limit to last N days")
    parser.add_argument("--type", "-t", choices=["message", "session", "research"], help="Filter by source type")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    # Check embeddings exist
    con = connect(args.db)
    emb_count = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if emb_count == 0:
        print("No embeddings found. Run embed.py first.", file=sys.stderr)
        con.close()
        sys.exit(1)

    # Load model and encode query
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    query_embedding = model.encode([args.query])[0].tolist()

    # Search
    results = search(con, query_embedding, args.type, args.project, args.days, args.limit)
    con.close()

    if not results:
        print("(no results)")
        return

    print(f"Semantic search: '{args.query}' ({len(results)} results)\n")
    table_rows = [(r["score"], r["type"], r["project"], r["date"], r["session"], r["preview"]) for r in results]
    print_table(table_rows, ["Score", "Type", "Project", "Date", "Session", "Preview"])


if __name__ == "__main__":
    main()
