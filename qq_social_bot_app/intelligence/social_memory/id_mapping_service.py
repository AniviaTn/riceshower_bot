"""ID mapping service: QQ号 ↔ 显示名, 群号 ↔ 群名.

Pure sqlite3 implementation (no SQLAlchemy). Two tables:
  - user_mappings: qq_id TEXT PK, display_name TEXT, updated_at REAL
  - group_mappings: group_id TEXT PK, group_name TEXT, updated_at REAL
"""
import os
import re
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS user_mappings (
    qq_id        TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_mappings (
    group_id   TEXT PRIMARY KEY,
    group_name TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# Only update once per day to avoid unnecessary writes (when name is unchanged)
_DEDUP_INTERVAL = 86400.0  # 24 hours


class IDMappingService:
    """Lightweight QQ-ID ↔ display-name mapping backed by SQLite."""

    def __init__(self, db_path: str, data_root: str | None = None):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._data_root = data_root
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # In-memory cache of last-update timestamps to avoid unnecessary writes
        self._user_ts_cache: dict[str, float] = {}
        self._group_ts_cache: dict[str, float] = {}
        # In-memory name caches for rename detection
        self._user_name_cache: dict[str, str] = {}
        self._group_name_cache: dict[str, str] = {}
        # Warmup name caches from SQLite
        self._warmup_caches()

    def _warmup_caches(self) -> None:
        """Pre-populate name caches from SQLite on startup."""
        for row in self._conn.execute(
                'SELECT qq_id, display_name, updated_at FROM user_mappings').fetchall():
            self._user_name_cache[row[0]] = row[1]
            self._user_ts_cache[row[0]] = row[2]
        for row in self._conn.execute(
                'SELECT group_id, group_name, updated_at FROM group_mappings').fetchall():
            self._group_name_cache[row[0]] = row[1]
            self._group_ts_cache[row[0]] = row[2]
        logger.debug('ID mapping caches warmed: %d users, %d groups',
                      len(self._user_name_cache), len(self._group_name_cache))

    # ----------------------------------------------------------
    # User mappings
    # ----------------------------------------------------------

    def get_user_name(self, qq_id: str) -> str | None:
        cached = self._user_name_cache.get(qq_id)
        if cached is not None:
            return cached
        row = self._conn.execute(
            'SELECT display_name FROM user_mappings WHERE qq_id = ?',
            (qq_id,),
        ).fetchone()
        if row:
            self._user_name_cache[qq_id] = row[0]
        return row[0] if row else None

    def set_user_name(self, qq_id: str, display_name: str) -> None:
        """Upsert user mapping. Skips write if name unchanged AND within dedup window.

        If the name changed, writes immediately and records the alias change.
        """
        if not qq_id or not display_name:
            return
        now = time.time()
        cached_name = self._user_name_cache.get(qq_id)
        name_changed = cached_name is not None and cached_name != display_name

        if not name_changed:
            last = self._user_ts_cache.get(qq_id, 0.0)
            if now - last < _DEDUP_INTERVAL:
                return

        self._conn.execute(
            'INSERT INTO user_mappings (qq_id, display_name, updated_at) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT(qq_id) DO UPDATE SET display_name=excluded.display_name, '
            'updated_at=excluded.updated_at',
            (qq_id, display_name, now),
        )
        self._conn.commit()
        self._user_ts_cache[qq_id] = now

        if name_changed:
            logger.info('User %s renamed: %s -> %s', qq_id, cached_name, display_name)
            self._record_alias_change('people', qq_id, cached_name, display_name)

        self._user_name_cache[qq_id] = display_name

    def get_all_user_mappings(self) -> dict[str, str]:
        """Return {qq_id: display_name} for all known users."""
        rows = self._conn.execute(
            'SELECT qq_id, display_name FROM user_mappings'
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ----------------------------------------------------------
    # Group mappings
    # ----------------------------------------------------------

    def get_group_name(self, group_id: str) -> str | None:
        cached = self._group_name_cache.get(group_id)
        if cached is not None:
            return cached
        row = self._conn.execute(
            'SELECT group_name FROM group_mappings WHERE group_id = ?',
            (group_id,),
        ).fetchone()
        if row:
            self._group_name_cache[group_id] = row[0]
        return row[0] if row else None

    def set_group_name(self, group_id: str, group_name: str) -> None:
        """Upsert group mapping. Skips write if name unchanged AND within dedup window."""
        if not group_id or not group_name:
            return
        now = time.time()
        cached_name = self._group_name_cache.get(group_id)
        name_changed = cached_name is not None and cached_name != group_name

        if not name_changed:
            last = self._group_ts_cache.get(group_id, 0.0)
            if now - last < _DEDUP_INTERVAL:
                return

        self._conn.execute(
            'INSERT INTO group_mappings (group_id, group_name, updated_at) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT(group_id) DO UPDATE SET group_name=excluded.group_name, '
            'updated_at=excluded.updated_at',
            (group_id, group_name, now),
        )
        self._conn.commit()
        self._group_ts_cache[group_id] = now

        if name_changed:
            logger.info('Group %s renamed: %s -> %s', group_id, cached_name, group_name)
            self._record_alias_change('groups', group_id, cached_name, group_name)

        self._group_name_cache[group_id] = group_name

    # ----------------------------------------------------------
    # Alias change tracking
    # ----------------------------------------------------------

    def _record_alias_change(self, scope: str, entity_id: str,
                              old_name: str, new_name: str) -> None:
        """Append an alias change record to the entity's profile.md."""
        if not self._data_root:
            return
        profile_path = os.path.join(self._data_root, scope, entity_id, 'profile.md')
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)

        ts = time.strftime('%Y-%m-%d %H:%M', time.localtime())
        alias_line = f'\n- 曾用名: {old_name} (更名于 {ts}, 现用名: {new_name})\n'

        try:
            # Read existing content
            existing = ''
            if os.path.isfile(profile_path):
                with open(profile_path, 'r', encoding='utf-8') as f:
                    existing = f.read()

            # Check if there's already an alias section
            if '## 曾用名' in existing:
                # Append to existing alias section
                with open(profile_path, 'a', encoding='utf-8') as f:
                    f.write(alias_line)
            else:
                # Create alias section
                with open(profile_path, 'a', encoding='utf-8') as f:
                    if existing and not existing.endswith('\n'):
                        f.write('\n')
                    f.write(f'\n## 曾用名{alias_line}')
        except Exception:
            logger.debug('Failed to record alias change for %s/%s',
                          scope, entity_id, exc_info=True)

    # ----------------------------------------------------------
    # Batch text replacement
    # ----------------------------------------------------------

    def resolve_ids_in_text(self, text: str) -> str:
        """Replace QQ number patterns in text with display names.

        Matches patterns like [HH:MM] 12345678: or sender_id references.
        """
        mappings = self.get_all_user_mappings()
        if not mappings:
            return text

        def _replace(match: re.Match) -> str:
            qq_id = match.group(0)
            name = mappings.get(qq_id)
            return name if name else qq_id

        # Match sequences of 5-12 digits that look like QQ IDs
        pattern = re.compile(r'\b(\d{5,12})\b')
        return pattern.sub(_replace, text)

    def close(self):
        self._conn.close()
