"""Raw message file storage.

Persists every GroupMessage as a JSONL line in a directory tree organised by
group → date → hour:

    {base_dir}/{group_id}/{YYYY-MM-DD}/{HH}.jsonl

Each line is a self-contained JSON object so the files can be searched with
standard CLI tools (grep, jq, etc.).
"""
import json
import os
import time
from typing import List

import aiofiles

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage

_DEFAULT_BASE_DIR = os.path.join(os.path.expanduser('~'), '.qq_bot_data', 'raw_messages')


class RawMessageStore:
    """Append-only JSONL store for raw group messages."""

    def __init__(self, base_dir: str = _DEFAULT_BASE_DIR):
        self._base_dir = base_dir

    # ----------------------------------------------------------
    # Write (sync)
    # ----------------------------------------------------------

    def append(self, msg: GroupMessage) -> str:
        """Append a single message.  Returns the file path written to."""
        path = self._resolve_path(msg.group_id, msg.timestamp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(msg.to_dict(), ensure_ascii=False)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        return path

    def append_batch(self, msgs: List[GroupMessage]) -> None:
        """Append multiple messages, grouping writes by file path."""
        by_path: dict[str, list] = {}
        for msg in msgs:
            path = self._resolve_path(msg.group_id, msg.timestamp)
            by_path.setdefault(path, []).append(msg)

        for path, group_msgs in by_path.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                for msg in group_msgs:
                    f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + '\n')

    # ----------------------------------------------------------
    # Write (async)
    # ----------------------------------------------------------

    async def async_append(self, msg: GroupMessage) -> str:
        """Append a single message asynchronously.  Returns the file path written to."""
        path = self._resolve_path(msg.group_id, msg.timestamp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(msg.to_dict(), ensure_ascii=False)
        async with aiofiles.open(path, 'a', encoding='utf-8') as f:
            await f.write(line + '\n')
        return path

    async def async_append_batch(self, msgs: List[GroupMessage]) -> None:
        """Append multiple messages asynchronously, grouping writes by file path."""
        by_path: dict[str, list] = {}
        for msg in msgs:
            path = self._resolve_path(msg.group_id, msg.timestamp)
            by_path.setdefault(path, []).append(msg)

        for path, group_msgs in by_path.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(path, 'a', encoding='utf-8') as f:
                for msg in group_msgs:
                    await f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + '\n')

    # ----------------------------------------------------------
    # Read
    # ----------------------------------------------------------

    def read_messages_since(self, group_id: str, since_ts: float,
                            until_ts: float | None = None) -> list[dict]:
        """Read messages from JSONL files for a group within a time range.

        Scans date/hour directories from since_ts to until_ts (default: now).
        Returns sorted list of message dicts.
        """
        if until_ts is None:
            until_ts = time.time()

        group_dir = os.path.join(self._base_dir, group_id)
        if not os.path.isdir(group_dir):
            return []

        messages: list[dict] = []

        # Iterate over date directories
        since_t = time.localtime(since_ts)
        until_t = time.localtime(until_ts)
        since_date = time.strftime('%Y-%m-%d', since_t)
        until_date = time.strftime('%Y-%m-%d', until_t)

        try:
            date_dirs = sorted(d for d in os.listdir(group_dir)
                               if os.path.isdir(os.path.join(group_dir, d)))
        except OSError:
            return []

        for date_str in date_dirs:
            if date_str < since_date or date_str > until_date:
                continue
            date_path = os.path.join(group_dir, date_str)
            try:
                hour_files = sorted(f for f in os.listdir(date_path)
                                    if f.endswith('.jsonl'))
            except OSError:
                continue

            for hour_file in hour_files:
                file_path = os.path.join(date_path, hour_file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                ts = msg.get('timestamp', 0)
                                if since_ts <= ts <= until_ts:
                                    messages.append(msg)
                            except (json.JSONDecodeError, TypeError):
                                continue
                except OSError:
                    continue

        messages.sort(key=lambda m: m.get('timestamp', 0))
        return messages

    def get_known_group_ids(self) -> list[str]:
        """List all group IDs that have raw message directories."""
        if not os.path.isdir(self._base_dir):
            return []
        try:
            return [d for d in os.listdir(self._base_dir)
                    if os.path.isdir(os.path.join(self._base_dir, d))]
        except OSError:
            return []

    # ----------------------------------------------------------
    # Path helpers
    # ----------------------------------------------------------

    def _resolve_path(self, group_id: str, timestamp: float) -> str:
        """Build file path: {base_dir}/{group_id}/{YYYY-MM-DD}/{HH}.jsonl"""
        t = time.localtime(timestamp)
        date_str = time.strftime('%Y-%m-%d', t)
        hour_str = time.strftime('%H', t)
        return os.path.join(self._base_dir, group_id, date_str, f'{hour_str}.jsonl')

    @property
    def base_dir(self) -> str:
        return self._base_dir
