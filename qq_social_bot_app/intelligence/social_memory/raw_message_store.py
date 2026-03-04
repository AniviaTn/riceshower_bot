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

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage

# Anchor to qq_social_bot_app/ root so the path is deterministic
# regardless of which directory the process is started from.
# raw_message_store.py is at: qq_social_bot_app/intelligence/social_memory/
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_DEFAULT_BASE_DIR = os.path.join(_APP_ROOT, 'data', 'raw_messages')


class RawMessageStore:
    """Append-only JSONL store for raw group messages."""

    def __init__(self, base_dir: str = _DEFAULT_BASE_DIR):
        self._base_dir = base_dir

    # ----------------------------------------------------------
    # Write
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
