"""Checkpoint store for summarization progress tracking.

Uses a SQLite table (summarization_checkpoints) in the shared mappings.db
to record the last-processed timestamp per group.
"""
import os
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA = """\
CREATE TABLE IF NOT EXISTS summarization_checkpoints (
    group_id          TEXT PRIMARY KEY,
    last_checkpoint   REAL NOT NULL,
    last_summary_path TEXT,
    updated_at        REAL NOT NULL
);
"""


class CheckpointStore:
    """SQLite-backed checkpoint storage for periodic summarization."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.executescript(_CHECKPOINT_SCHEMA)
        self._conn.commit()

    def get_checkpoint(self, group_id: str) -> dict | None:
        """Get the checkpoint for a group.

        Returns {'group_id', 'last_checkpoint', 'last_summary_path', 'updated_at'}
        or None if no checkpoint exists.
        """
        row = self._conn.execute(
            'SELECT group_id, last_checkpoint, last_summary_path, updated_at '
            'FROM summarization_checkpoints WHERE group_id = ?',
            (group_id,),
        ).fetchone()
        if not row:
            return None
        return {
            'group_id': row[0],
            'last_checkpoint': row[1],
            'last_summary_path': row[2],
            'updated_at': row[3],
        }

    def set_checkpoint(self, group_id: str, ts: float,
                       path: str | None = None) -> None:
        """Upsert the checkpoint for a group."""
        now = time.time()
        self._conn.execute(
            'INSERT INTO summarization_checkpoints '
            '(group_id, last_checkpoint, last_summary_path, updated_at) '
            'VALUES (?, ?, ?, ?) '
            'ON CONFLICT(group_id) DO UPDATE SET '
            'last_checkpoint=excluded.last_checkpoint, '
            'last_summary_path=excluded.last_summary_path, '
            'updated_at=excluded.updated_at',
            (group_id, ts, path, now),
        )
        self._conn.commit()

    def get_all_checkpoints(self) -> list[dict]:
        """Return checkpoints for all groups."""
        rows = self._conn.execute(
            'SELECT group_id, last_checkpoint, last_summary_path, updated_at '
            'FROM summarization_checkpoints'
        ).fetchall()
        return [
            {
                'group_id': r[0],
                'last_checkpoint': r[1],
                'last_summary_path': r[2],
                'updated_at': r[3],
            }
            for r in rows
        ]

    def close(self):
        self._conn.close()
