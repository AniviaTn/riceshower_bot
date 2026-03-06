"""Layer 1: Redis-backed WorkingMemory for immediate conversation context.

Stores recent messages, current topic, mood, and active participants per group.
Data expires via TTL so stale sessions clean themselves up.
"""
import json
import logging
import time
from typing import List, Optional

import redis.asyncio as aioredis

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage

logger = logging.getLogger(__name__)


def format_message_time(timestamp: float, now: float = None) -> str:
    """Format a message timestamp for display.

    - Today:     "14:32"
    - Yesterday: "昨天 22:15"
    - Older:     "3月2日 09:30"
    """
    if not timestamp or timestamp <= 0:
        return '??:??'
    if now is None:
        now = time.time()
    msg_t = time.localtime(timestamp)
    now_t = time.localtime(now)

    # Same calendar day
    if msg_t.tm_year == now_t.tm_year and msg_t.tm_yday == now_t.tm_yday:
        return time.strftime('%H:%M', msg_t)

    # Yesterday
    yest_t = time.localtime(now - 86400)
    if msg_t.tm_year == yest_t.tm_year and msg_t.tm_yday == yest_t.tm_yday:
        return '昨天 ' + time.strftime('%H:%M', msg_t)

    # Older
    return f'{msg_t.tm_mon}月{msg_t.tm_mday}日 {time.strftime("%H:%M", msg_t)}'


class WorkingMemory:
    """Immediate conversation memory backed by async Redis.

    Key scheme:
        wm:{group_id}:messages     -> List (JSON-encoded per entry)
        wm:{group_id}:topic        -> String
        wm:{group_id}:mood         -> String
        wm:{group_id}:participants -> Set of sender_name strings
    """

    def __init__(self, redis_url: str = 'redis://localhost:6379/0',
                 max_messages: int = 200, ttl_seconds: int = 7200):
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url, decode_responses=True)
        self._max_messages = max_messages
        self._ttl = ttl_seconds

    # ----------------------------------------------------------
    # Key helpers
    # ----------------------------------------------------------

    @staticmethod
    def _key(group_id: str, suffix: str) -> str:
        return f'wm:{group_id}:{suffix}'

    async def _refresh_ttl(self, group_id: str):
        """Reset TTL on all keys for this group."""
        for suffix in ('messages', 'topic', 'mood', 'participants'):
            key = self._key(group_id, suffix)
            await self._redis.expire(key, self._ttl)

    # ----------------------------------------------------------
    # Write
    # ----------------------------------------------------------

    async def add_message(self, group_id: str, msg: GroupMessage):
        """Append a message, trim to max_messages, reset TTL."""
        entry = {
            'sender_id': msg.sender_id,
            'sender_name': msg.sender_name,
            'content': msg.content,
            'timestamp': msg.timestamp,
            'message_id': msg.message_id,
            'reply_to': msg.reply_to,
            'image_urls': msg.image_urls if hasattr(msg, 'image_urls') else [],
        }
        msg_key = self._key(group_id, 'messages')
        pipe = self._redis.pipeline()
        pipe.rpush(msg_key, json.dumps(entry, ensure_ascii=False))
        pipe.ltrim(msg_key, -self._max_messages, -1)
        pipe.expire(msg_key, self._ttl)
        # track participant
        part_key = self._key(group_id, 'participants')
        pipe.sadd(part_key, msg.sender_name)
        pipe.expire(part_key, self._ttl)
        await pipe.execute()

    async def add_messages(self, group_id: str, msgs: List[GroupMessage]):
        """Append multiple messages in a single Redis pipeline."""
        if not msgs:
            return
        msg_key = self._key(group_id, 'messages')
        part_key = self._key(group_id, 'participants')
        pipe = self._redis.pipeline()
        for msg in msgs:
            entry = {
                'sender_id': msg.sender_id,
                'sender_name': msg.sender_name,
                'content': msg.content,
                'timestamp': msg.timestamp,
                'message_id': msg.message_id,
                'reply_to': msg.reply_to,
                'image_urls': msg.image_urls if hasattr(msg, 'image_urls') else [],
            }
            pipe.rpush(msg_key, json.dumps(entry, ensure_ascii=False))
            pipe.sadd(part_key, msg.sender_name)
        pipe.ltrim(msg_key, -self._max_messages, -1)
        pipe.expire(msg_key, self._ttl)
        pipe.expire(part_key, self._ttl)
        await pipe.execute()

    async def update_topic(self, group_id: str, topic: str, mood: str = None):
        """Update current topic and optionally mood."""
        pipe = self._redis.pipeline()
        pipe.set(self._key(group_id, 'topic'), topic, ex=self._ttl)
        if mood:
            pipe.set(self._key(group_id, 'mood'), mood, ex=self._ttl)
        await pipe.execute()

    # ----------------------------------------------------------
    # Read
    # ----------------------------------------------------------

    async def get_recent_messages(self, group_id: str,
                                  limit: Optional[int] = None) -> List[dict]:
        """Return recent messages as list of dicts, oldest first."""
        msg_key = self._key(group_id, 'messages')
        n = limit or self._max_messages
        raw = await self._redis.lrange(msg_key, -n, -1)
        messages = []
        for item in raw:
            try:
                messages.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        return messages

    async def get_context(self, group_id: str, id_mapping=None) -> dict:
        """Build the full working-memory context for a group.

        Args:
            group_id: The group to get context for.
            id_mapping: Optional IDMappingService instance. When provided,
                sender names are resolved to their current display name,
                falling back to the stored sender_name.

        Returns:
            {
                'recent_messages': List[dict],
                'recent_messages_text': str,      # formatted for prompt injection
                'current_topic': str,
                'participants': List[str],
                'mood': str,
            }
        """
        messages = await self.get_recent_messages(group_id)
        topic = await self._redis.get(self._key(group_id, 'topic')) or ''
        mood = await self._redis.get(self._key(group_id, 'mood')) or ''
        participants = list(
            await self._redis.smembers(self._key(group_id, 'participants')) or [])

        # Build formatted text with smart time display
        now = time.time()
        lines = []
        for m in messages:
            ts_str = format_message_time(m.get('timestamp', 0), now)
            # Use current display name from id_mapping if available
            sender_id = m.get('sender_id', '')
            stored_name = m.get('sender_name', '???')
            if id_mapping and sender_id:
                name = id_mapping.get_user_name(sender_id) or stored_name
            else:
                name = stored_name
            content = m.get('content', '')
            lines.append(f'[{ts_str}] {name}: {content}')
        text = '\n'.join(lines)

        return {
            'recent_messages': messages,
            'recent_messages_text': text,
            'current_topic': topic,
            'participants': participants,
            'mood': mood,
        }

    # ----------------------------------------------------------
    # Query helpers
    # ----------------------------------------------------------

    async def get_message_count(self, group_id: str) -> int:
        """Return the number of messages currently stored for *group_id*."""
        return await self._redis.llen(self._key(group_id, 'messages'))

    async def trim_oldest_messages(self, group_id: str, count: int) -> None:
        """Remove the *count* oldest (leftmost) messages for *group_id*.

        After trimming, TTL is refreshed so the remaining messages keep
        their normal expiry window.
        """
        if count <= 0:
            return
        msg_key = self._key(group_id, 'messages')
        # LTRIM keeps elements from index `count` to the end, effectively
        # removing the first `count` entries.
        await self._redis.ltrim(msg_key, count, -1)
        await self._redis.expire(msg_key, self._ttl)

    # ----------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------

    async def clear_group(self, group_id: str):
        """Explicitly clear all working-memory data for a group."""
        keys = [self._key(group_id, s)
                for s in ('messages', 'topic', 'mood', 'participants')]
        await self._redis.delete(*keys)

    async def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return await self._redis.ping()
        except Exception:
            return False
