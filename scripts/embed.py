#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.2", "sentence-transformers>=3.0", "torch"]
# ///
"""claude-warehouse: incremental vector embedding for semantic search."""

import argparse
import time
from pathlib import Path

import duckdb

CLAUDE_DIR = Path.home() / ".claude"
DB_PATH = CLAUDE_DIR / "claude.duckdb"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 256
MIN_TEXT_LEN = 30
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def init_db(con: duckdb.DuckDBPyConnection):
    con.execute(SCHEMA_PATH.read_text())
    con.execute("INSTALL vss; LOAD vss")
    con.execute("SET hnsw_enable_experimental_persistence = true")


def load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def chunk_text(text: str) -> list[tuple[int, str]]:
    if len(text) <= CHUNK_SIZE:
        return [(0, text)]
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append((idx, text[start:end]))
        start = end - CHUNK_OVERLAP
        idx += 1
    return chunks


def batch_encode(model, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]


# ---------------------------------------------------------------------------
# Embedding pipelines
# ---------------------------------------------------------------------------

def count_unembedded(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    msg_count = con.execute("""
        SELECT COUNT(*) FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message'
            AND e.source_id = m.session_id || ':' || m.uuid
        WHERE e.source_id IS NULL
            AND m.text_content IS NOT NULL
            AND LENGTH(m.text_content) >= ?
    """, [MIN_TEXT_LEN]).fetchone()[0]

    sess_count = con.execute("""
        SELECT COUNT(*) FROM sessions s
        LEFT JOIN embeddings e ON e.source_type = 'session'
            AND e.source_id = s.session_id
        WHERE e.source_id IS NULL
            AND s.first_prompt IS NOT NULL
            AND LENGTH(s.first_prompt) >= ?
    """, [MIN_TEXT_LEN]).fetchone()[0]

    res_count = con.execute("""
        SELECT COUNT(*) FROM research_history r
        LEFT JOIN embeddings e ON e.source_type = 'research'
            AND e.source_id = r.file_path AND e.chunk_idx = 0
        WHERE e.source_id IS NULL
            AND r.content IS NOT NULL
            AND LENGTH(r.content) >= ?
    """, [MIN_TEXT_LEN]).fetchone()[0]

    return {"message": msg_count, "session": sess_count, "research": res_count}


def embed_messages(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    rows = con.execute("""
        SELECT m.session_id, m.uuid, m.text_content
        FROM messages m
        LEFT JOIN embeddings e ON e.source_type = 'message'
            AND e.source_id = m.session_id || ':' || m.uuid
        WHERE e.source_id IS NULL
            AND m.text_content IS NOT NULL
            AND LENGTH(m.text_content) >= ?
    """, [MIN_TEXT_LEN]).fetchall()

    if not rows:
        return 0

    texts = [r[2] for r in rows]
    embeddings = batch_encode(model, texts)

    batch = []
    for (sid, uuid, text), emb in zip(rows, embeddings):
        source_id = f"{sid}:{uuid}"
        preview = text[:200]
        batch.append(("message", source_id, 0, preview, emb))

    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Messages: {len(batch)} embedded")
    return len(batch)


def embed_sessions(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    rows = con.execute("""
        SELECT s.session_id, s.first_prompt
        FROM sessions s
        LEFT JOIN embeddings e ON e.source_type = 'session'
            AND e.source_id = s.session_id
        WHERE e.source_id IS NULL
            AND s.first_prompt IS NOT NULL
            AND LENGTH(s.first_prompt) >= ?
    """, [MIN_TEXT_LEN]).fetchall()

    if not rows:
        return 0

    texts = [r[1] for r in rows]
    embeddings = batch_encode(model, texts)

    batch = []
    for (sid, text), emb in zip(rows, embeddings):
        batch.append(("session", sid, 0, text[:200], emb))

    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Sessions: {len(batch)} embedded")
    return len(batch)


def embed_research(con: duckdb.DuckDBPyConnection, model, verbose: bool = False):
    rows = con.execute("""
        SELECT r.file_path, r.content
        FROM research_history r
        LEFT JOIN embeddings e ON e.source_type = 'research'
            AND e.source_id = r.file_path AND e.chunk_idx = 0
        WHERE e.source_id IS NULL
            AND r.content IS NOT NULL
            AND LENGTH(r.content) >= ?
    """, [MIN_TEXT_LEN]).fetchall()

    if not rows:
        return 0

    all_chunks = []  # (source_id, chunk_idx, text)
    for file_path, content in rows:
        for chunk_idx, chunk_text_str in chunk_text(content):
            all_chunks.append((file_path, chunk_idx, chunk_text_str))

    texts = [c[2] for c in all_chunks]
    embeddings = batch_encode(model, texts)

    batch = []
    for (source_id, chunk_idx, text), emb in zip(all_chunks, embeddings):
        batch.append(("research", source_id, chunk_idx, text[:200], emb))

    con.executemany(
        "INSERT INTO embeddings VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
        batch,
    )
    if verbose:
        print(f"  Research: {len(batch)} chunks embedded ({len(rows)} docs)")
    return len(batch)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def clean_stale_embeddings(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    """Remove embeddings for messages/sessions that were re-synced (deleted+reinserted)."""
    # Messages: find embeddings referencing non-existent messages
    stale_msgs = con.execute("""
        SELECT e.source_id FROM embeddings e
        WHERE e.source_type = 'message'
        AND NOT EXISTS (
            SELECT 1 FROM messages m
            WHERE e.source_id = m.session_id || ':' || m.uuid
        )
    """).fetchall()

    # Sessions: find embeddings referencing non-existent sessions
    stale_sess = con.execute("""
        SELECT e.source_id FROM embeddings e
        WHERE e.source_type = 'session'
        AND NOT EXISTS (
            SELECT 1 FROM sessions s WHERE e.source_id = s.session_id
        )
    """).fetchall()

    # Research: find embeddings referencing non-existent research
    stale_res = con.execute("""
        SELECT DISTINCT e.source_id FROM embeddings e
        WHERE e.source_type = 'research'
        AND NOT EXISTS (
            SELECT 1 FROM research_history r WHERE e.source_id = r.file_path
        )
    """).fetchall()

    total = 0
    if stale_msgs:
        ids = [r[0] for r in stale_msgs]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'message' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)
    if stale_sess:
        ids = [r[0] for r in stale_sess]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'session' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)
    if stale_res:
        ids = [r[0] for r in stale_res]
        con.executemany(
            "DELETE FROM embeddings WHERE source_type = 'research' AND source_id = ?",
            [(i,) for i in ids],
        )
        total += len(ids)

    if verbose and total:
        print(f"  Cleaned {total} stale embeddings")
    return total


# ---------------------------------------------------------------------------
# HNSW index
# ---------------------------------------------------------------------------

def rebuild_hnsw(con: duckdb.DuckDBPyConnection, verbose: bool = False):
    t0 = time.time()
    con.execute("DROP INDEX IF EXISTS idx_embeddings_hnsw")
    row_count = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if row_count > 0:
        con.execute("""
            CREATE INDEX idx_embeddings_hnsw
            ON embeddings USING HNSW (embedding)
            WITH (metric = 'cosine')
        """)
    if verbose:
        print(f"  HNSW index: {row_count} vectors ({time.time() - t0:.1f}s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="claude-warehouse embed")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--full", action="store_true", help="Re-embed everything")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    t0 = time.time()

    if not Path(args.db).exists():
        print("Database not found. Run sync.py first.")
        return

    con = duckdb.connect(args.db)
    init_db(con)

    if args.full:
        con.execute("DELETE FROM embeddings")
        if args.verbose:
            print("Cleared all embeddings for full re-embed")

    # Early exit check
    counts = count_unembedded(con)
    stale = clean_stale_embeddings(con, args.verbose)
    total_work = sum(counts.values()) + stale

    if args.verbose:
        print(f"Unembedded: {counts['message']} msgs, {counts['session']} sessions, {counts['research']} research")

    if total_work == 0 and not args.full:
        if args.verbose:
            print("Nothing to embed.")
        con.close()
        return

    # Load model only when there's work
    if args.verbose:
        print("Loading model...")
    model = load_model()

    # Re-count after stale cleanup (stale cleanup may have freed source_ids)
    if stale > 0:
        counts = count_unembedded(con)

    # Embed
    n_msg = embed_messages(con, model, args.verbose)
    n_sess = embed_sessions(con, model, args.verbose)
    n_res = embed_research(con, model, args.verbose)

    total = n_msg + n_sess + n_res
    if total > 0 or stale > 0:
        rebuild_hnsw(con, args.verbose)

    con.close()

    if args.verbose:
        print(f"Done in {time.time() - t0:.1f}s — {total} new embeddings")


if __name__ == "__main__":
    main()
