"""
SQLite database models and operations for Suno Manager.
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "suno_manager.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                lyrics TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                negative_tags TEXT DEFAULT '',
                make_instrumental BOOLEAN DEFAULT 0,
                model TEXT DEFAULT 'chirp-crow',
                status TEXT DEFAULT 'pending',
                error_message TEXT DEFAULT '',
                batch_name TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id INTEGER NOT NULL,
                suno_id TEXT NOT NULL UNIQUE,
                audio_url TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                video_url TEXT DEFAULT '',
                duration REAL DEFAULT 0,
                has_silence BOOLEAN DEFAULT NULL,
                silence_details TEXT DEFAULT '',
                suno_status TEXT DEFAULT 'submitted',
                error_message TEXT DEFAULT '',
                downloaded BOOLEAN DEFAULT 0,
                file_path TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (song_id) REFERENCES songs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Migrations: add columns if missing (for existing databases)
        _migrate_add_column(conn, "songs", "error_message", "TEXT DEFAULT ''")


def _migrate_add_column(conn, table, column, col_type):
    """Add a column if it doesn't exist (safe migration)."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ─── Song CRUD ───────────────────────────────────────────────

def create_song(title, lyrics, tags, negative_tags="", make_instrumental=False, model="chirp-crow", batch_name=""):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO songs (title, lyrics, tags, negative_tags, make_instrumental, model, batch_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, lyrics, tags, negative_tags, int(make_instrumental), model, batch_name)
        )
        return cursor.lastrowid


def get_songs(status=None, page=1, per_page=20, search=None):
    with get_db() as conn:
        where_clauses = []
        params = []

        if status and status != "all":
            where_clauses.append("s.status = ?")
            params.append(status)

        if search:
            where_clauses.append("(s.title LIKE ? OR s.tags LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Count total
        count = conn.execute(f"SELECT COUNT(*) FROM songs s {where_sql}", params).fetchone()[0]

        # Get paginated results with generation info
        offset = (page - 1) * per_page
        rows = conn.execute(f"""
            SELECT s.*,
                   g.suno_id, g.audio_url, g.image_url, g.video_url,
                   g.duration, g.has_silence, g.silence_details,
                   g.suno_status, g.downloaded, g.file_path,
                   COALESCE(g.error_message, s.error_message) as error_message,
                   g.id as gen_id
            FROM songs s
            LEFT JOIN generations g ON g.song_id = s.id
            {where_sql}
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()

        return {
            "songs": [dict(r) for r in rows],
            "total": count,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (count + per_page - 1) // per_page)
        }


def get_song(song_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT s.*,
                   g.suno_id, g.audio_url, g.image_url, g.video_url,
                   g.duration, g.has_silence, g.silence_details,
                   g.suno_status, g.downloaded, g.file_path,
                   COALESCE(g.error_message, s.error_message) as error_message,
                   g.id as gen_id
            FROM songs s
            LEFT JOIN generations g ON g.song_id = s.id
            WHERE s.id = ?
        """, (song_id,)).fetchone()
        return dict(row) if row else None


def update_song_status(song_id, status, error_message=None):
    with get_db() as conn:
        if error_message is not None:
            conn.execute(
                "UPDATE songs SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error_message, song_id)
            )
        else:
            conn.execute(
                "UPDATE songs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, song_id)
            )


def delete_song(song_id):
    """Delete a song and all its generations (CASCADE)."""
    with get_db() as conn:
        # Get file paths before deleting (for cleanup)
        rows = conn.execute(
            "SELECT file_path FROM generations WHERE song_id = ? AND file_path != ''",
            (song_id,)
        ).fetchall()
        file_paths = [r["file_path"] for r in rows]
        conn.execute("DELETE FROM generations WHERE song_id = ?", (song_id,))
        conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        return file_paths


def delete_songs_batch(song_ids: list[int]):
    """Delete multiple songs and their generations."""
    with get_db() as conn:
        placeholders = ",".join("?" * len(song_ids))
        rows = conn.execute(
            f"SELECT file_path FROM generations WHERE song_id IN ({placeholders}) AND file_path != ''",
            song_ids
        ).fetchall()
        file_paths = [r["file_path"] for r in rows]
        conn.execute(f"DELETE FROM generations WHERE song_id IN ({placeholders})", song_ids)
        conn.execute(f"DELETE FROM songs WHERE id IN ({placeholders})", song_ids)
        return file_paths


def get_pending_songs():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM songs WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_submitted_songs():
    """Songs that are submitted but not yet complete."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.*, g.suno_id, g.suno_status
            FROM songs s
            JOIN generations g ON g.song_id = s.id
            WHERE s.status = 'submitted' AND g.suno_status NOT IN ('complete', 'error')
            ORDER BY s.id ASC
        """).fetchall()
        return [dict(r) for r in rows]


# ─── Generation CRUD ─────────────────────────────────────────

def create_generation(song_id, suno_id, suno_status="submitted"):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO generations (song_id, suno_id, suno_status)
               VALUES (?, ?, ?)""",
            (song_id, suno_id, suno_status)
        )
        return cursor.lastrowid


def update_generation(suno_id, **kwargs):
    with get_db() as conn:
        set_parts = []
        values = []
        for key, val in kwargs.items():
            set_parts.append(f"{key} = ?")
            values.append(val)
        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        values.append(suno_id)

        conn.execute(
            f"UPDATE generations SET {', '.join(set_parts)} WHERE suno_id = ?",
            values
        )


def get_generation_by_suno_id(suno_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM generations WHERE suno_id = ?", (suno_id,)
        ).fetchone()
        return dict(row) if row else None


def get_incomplete_generations():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT g.*, s.title, s.id as song_id
            FROM generations g
            JOIN songs s ON s.id = g.song_id
            WHERE g.suno_status NOT IN ('complete', 'error')
        """).fetchall()
        return [dict(r) for r in rows]


def get_recent_generations(limit=10):
    """Get recent generations with song info for dashboard.
    Also includes songs that have no generations (e.g. error/pending songs)."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT g.suno_id, g.suno_status, g.duration, g.audio_url,
                   g.has_silence, g.downloaded, g.created_at,
                   s.id as song_id, s.title, s.tags, s.status as song_status
            FROM generations g
            JOIN songs s ON s.id = g.song_id

            UNION ALL

            SELECT NULL as suno_id, s.status as suno_status, NULL as duration,
                   NULL as audio_url, NULL as has_silence, 0 as downloaded,
                   s.created_at, s.id as song_id, s.title, s.tags, s.status as song_status
            FROM songs s
            WHERE NOT EXISTS (SELECT 1 FROM generations g WHERE g.song_id = s.id)

            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_downloadable_generations(min_duration=180):
    """Complete generations that are not yet downloaded and meet the min duration."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT g.*, s.title
            FROM generations g
            JOIN songs s ON s.id = g.song_id
            WHERE g.suno_status = 'complete'
              AND g.downloaded = 0
              AND g.duration >= ?
              AND g.audio_url != ''
        """, (min_duration,)).fetchall()
        return [dict(r) for r in rows]


# ─── Stats ────────────────────────────────────────────────────

def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE status = 'complete'"
        ).fetchone()[0]
        processing = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE status IN ('submitted', 'processing')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE status = 'pending'"
        ).fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE status = 'error'"
        ).fetchone()[0]
        # Generation stats
        total_gens = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        completed_gens = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE suno_status = 'complete'"
        ).fetchone()[0]
        processing_gens = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE suno_status NOT IN ('complete', 'error')"
        ).fetchone()[0]
        error_gens = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE suno_status = 'error'"
        ).fetchone()[0]
        return {
            "total": total,
            "completed": completed,
            "processing": processing,
            "pending": pending,
            "errors": errors,
            "total_gens": total_gens,
            "completed_gens": completed_gens,
            "processing_gens": processing_gens,
            "error_gens": error_gens,
        }


# ─── Settings ─────────────────────────────────────────────────

def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )


def get_all_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
