"""Markdown file read/write service for the memory system.

All paths are relative to data_root (~/.qq_bot_data/).
Security: rejects '..', absolute paths, and write/delete to persona/ directory.
All methods are synchronous; async callers should use asyncio.to_thread().
"""
import fnmatch
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


class MarkdownMemoryService:
    """Read/write Markdown memory files under data_root."""

    def __init__(self, data_root: str):
        self._root = os.path.expanduser(data_root)

    @property
    def data_root(self) -> str:
        return self._root

    # ----------------------------------------------------------
    # Path validation
    # ----------------------------------------------------------

    def _resolve(self, rel_path: str) -> str:
        """Resolve a relative path to an absolute path, with safety checks."""
        if os.path.isabs(rel_path):
            raise ValueError(f'Absolute paths not allowed: {rel_path}')
        if '..' in rel_path.split('/'):
            raise ValueError(f'Path traversal not allowed: {rel_path}')
        return os.path.join(self._root, rel_path)

    @staticmethod
    def _assert_writable(rel_path: str) -> None:
        """Raise if the path is in a read-only zone."""
        if rel_path.startswith('persona/') or rel_path.startswith('persona\\'):
            raise PermissionError(f'Cannot write to persona/ directory: {rel_path}')

    # ----------------------------------------------------------
    # CRUD
    # ----------------------------------------------------------

    def read_file(self, rel_path: str) -> Optional[str]:
        """Read a file and return its content, or None if not found."""
        abs_path = self._resolve(rel_path)
        if not os.path.isfile(abs_path):
            return None
        with open(abs_path, 'r', encoding='utf-8') as f:
            return f.read()

    def write_file(self, rel_path: str, content: str) -> str:
        """Write content to a file (create dirs as needed). Returns abs path."""
        self._assert_writable(rel_path)
        abs_path = self._resolve(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return abs_path

    def delete_file(self, rel_path: str) -> bool:
        """Delete a file. Returns True if deleted, False if not found."""
        self._assert_writable(rel_path)
        abs_path = self._resolve(rel_path)
        if os.path.isfile(abs_path):
            os.remove(abs_path)
            return True
        return False

    def file_exists(self, rel_path: str) -> bool:
        abs_path = self._resolve(rel_path)
        return os.path.isfile(abs_path)

    # ----------------------------------------------------------
    # Listing
    # ----------------------------------------------------------

    def list_files(self, rel_dir: str, pattern: str = '*.md') -> List[str]:
        """List files in a directory matching a glob pattern.

        Returns list of relative paths (relative to data_root).
        """
        abs_dir = self._resolve(rel_dir)
        if not os.path.isdir(abs_dir):
            return []
        results = []
        for entry in os.listdir(abs_dir):
            if fnmatch.fnmatch(entry, pattern):
                full = os.path.join(abs_dir, entry)
                if os.path.isfile(full):
                    results.append(os.path.join(rel_dir, entry))
        return results

    def get_recent_files(self, rel_dir: str, limit: int = 5,
                         exclude_profile: bool = True) -> List[str]:
        """Return the most recently modified .md files in a directory.

        Sorted by mtime descending. Excludes profile.md by default.
        Returns relative paths.
        """
        abs_dir = self._resolve(rel_dir)
        if not os.path.isdir(abs_dir):
            return []

        entries = []
        for entry in os.listdir(abs_dir):
            if not entry.endswith('.md'):
                continue
            if exclude_profile and entry == 'profile.md':
                continue
            full = os.path.join(abs_dir, entry)
            if os.path.isfile(full):
                entries.append((os.path.getmtime(full), os.path.join(rel_dir, entry)))

        entries.sort(key=lambda x: x[0], reverse=True)
        return [path for _, path in entries[:limit]]

    # ----------------------------------------------------------
    # Bulk enumeration (for indexing)
    # ----------------------------------------------------------

    def enumerate_indexable_files(self) -> List[str]:
        """Return all .md files that should be indexed by the embedding service.

        Includes: people/{id}/*.md (excl profile.md), groups/{id}/*.md (excl profile.md), notes/*.md
        Excludes: persona/*.md, */profile.md, raw_messages/
        """
        results = []

        # people/{user_id}/*.md (excluding profile.md)
        people_dir = os.path.join(self._root, 'people')
        if os.path.isdir(people_dir):
            for user_id in os.listdir(people_dir):
                user_dir = os.path.join(people_dir, user_id)
                if not os.path.isdir(user_dir):
                    continue
                for fname in os.listdir(user_dir):
                    if fname.endswith('.md') and fname != 'profile.md':
                        results.append(f'people/{user_id}/{fname}')

        # groups/{group_id}/*.md (excluding profile.md)
        groups_dir = os.path.join(self._root, 'groups')
        if os.path.isdir(groups_dir):
            for group_id in os.listdir(groups_dir):
                group_dir = os.path.join(groups_dir, group_id)
                if not os.path.isdir(group_dir):
                    continue
                for fname in os.listdir(group_dir):
                    if fname.endswith('.md') and fname != 'profile.md':
                        results.append(f'groups/{group_id}/{fname}')

        # notes/*.md
        notes_dir = os.path.join(self._root, 'notes')
        if os.path.isdir(notes_dir):
            for fname in os.listdir(notes_dir):
                if fname.endswith('.md'):
                    results.append(f'notes/{fname}')

        return results

    @staticmethod
    def should_index(rel_path: str) -> bool:
        """Check if a file path should be indexed (not persona, not profile.md)."""
        if rel_path.startswith('persona/'):
            return False
        if os.path.basename(rel_path) == 'profile.md':
            return False
        if rel_path.startswith('raw_messages/'):
            return False
        parts = rel_path.split('/')
        if parts[0] in ('people', 'groups', 'notes'):
            return rel_path.endswith('.md')
        return False
