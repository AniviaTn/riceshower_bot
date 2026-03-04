"""Auto-detect and track conversation topics via keyword extraction.

Uses Redis sorted sets to maintain per-group topic heat maps.
Each topic is identified by a set of keywords and decays over time.
"""
import hashlib
import json
import logging
import math
import re
import time
from collections import Counter
from typing import Dict, List, Optional

import redis

logger = logging.getLogger(__name__)

# Maximum topics tracked per group
MAX_TOPICS = 10
# Topic TTL in Redis (4 hours)
TOPIC_TTL = 4 * 3600
# Heat parameters
INITIAL_HEAT = 0.8
HEAT_BOOST = 0.15
HEAT_CAP = 1.0
HEAT_THRESHOLD = 0.1
# Time-based decay: topic heat halves every DECAY_HALF_LIFE seconds of silence.
# 300s = 5 min → active discussions stay warm, idle topics fade in ~15-20 min.
DECAY_HALF_LIFE = 300.0
# Jaccard similarity threshold to match an existing topic
JACCARD_THRESHOLD = 0.3

# Regex for CJK characters
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+')
# Regex for ASCII words (2+ chars)
_ASCII_WORD_RE = re.compile(r'[a-zA-Z]{2,}')
# Common stop words to filter out
_STOP_WORDS = frozenset([
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
    '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着',
    '没有', '看', '好', '自己', '这', '他', '她', '它', '们', '那', '吗',
    '吧', '啊', '呢', '哈', '嗯', '哦', '呀', '么', '啦', '嘛', '噢',
    'the', 'is', 'at', 'in', 'on', 'to', 'and', 'or', 'of', 'for',
    'it', 'be', 'as', 'do', 'an', 'so', 'if', 'no', 'by', 'he', 'we',
])


def extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """Extract keywords from text.

    Tries jieba.analyse first; falls back to regex-based CJK + ASCII
    frequency extraction.
    """
    try:
        import jieba.analyse
        tags = jieba.analyse.extract_tags(text, topK=top_k)
        if tags:
            return tags
    except ImportError:
        pass

    # Fallback: frequency-based extraction
    tokens = []

    # CJK bigrams/trigrams
    cjk_chunks = _CJK_RE.findall(text)
    for chunk in cjk_chunks:
        # Single chars (if chunk is one char)
        if len(chunk) == 1 and chunk not in _STOP_WORDS:
            tokens.append(chunk)
        # Bigrams
        for i in range(len(chunk) - 1):
            bigram = chunk[i:i + 2]
            if bigram not in _STOP_WORDS:
                tokens.append(bigram)

    # ASCII words
    ascii_words = _ASCII_WORD_RE.findall(text.lower())
    for w in ascii_words:
        if w not in _STOP_WORDS:
            tokens.append(w)

    if not tokens:
        return []

    counter = Counter(tokens)
    return [word for word, _ in counter.most_common(top_k)]


def _topic_id(keywords: List[str]) -> str:
    """Generate a stable topic ID from sorted keywords."""
    key = '|'.join(sorted(keywords))
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


class TopicStateManager:
    """Auto-detect and track conversation topics per group using Redis.

    Redis key scheme:
        ts:{group_id}:topics          -> sorted set (member=topic_id, score=heat)
        ts:{group_id}:{topic_id}      -> hash {keywords, participants, summary, state}
    """

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    # ----------------------------------------------------------
    # Key helpers
    # ----------------------------------------------------------

    @staticmethod
    def _topics_key(group_id: str) -> str:
        return f'ts:{group_id}:topics'

    @staticmethod
    def _topic_detail_key(group_id: str, topic_id: str) -> str:
        return f'ts:{group_id}:{topic_id}'

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def detect_and_update(self, group_id: str, message: dict) -> Optional[str]:
        """Extract keywords from a message and update topic state.

        Args:
            group_id: The group to track topics for.
            message: Dict with at least 'content' and 'sender_name'.
                     Optional 'timestamp' (epoch float) for time-based decay.

        Returns:
            The topic_id that was updated or created, or None.
        """
        content = message.get('content', '')
        sender = message.get('sender_name', '')
        msg_time = message.get('timestamp') or time.time()

        keywords = extract_keywords(content)
        if not keywords:
            self.cool_down(group_id, current_time=msg_time)
            return None

        kw_set = set(keywords)

        # Find best matching existing topic
        best_topic_id = None
        best_score = 0.0

        topics_key = self._topics_key(group_id)
        existing_topics = self._redis.zrangebyscore(
            topics_key, HEAT_THRESHOLD, '+inf')

        for tid in existing_topics:
            detail_key = self._topic_detail_key(group_id, tid)
            stored_kw_raw = self._redis.hget(detail_key, 'keywords')
            if not stored_kw_raw:
                continue
            try:
                stored_kw = set(json.loads(stored_kw_raw))
            except (json.JSONDecodeError, TypeError):
                continue

            sim = _jaccard(kw_set, stored_kw)
            if sim > best_score:
                best_score = sim
                best_topic_id = tid

        if best_score >= JACCARD_THRESHOLD and best_topic_id:
            self._boost_topic(group_id, best_topic_id, keywords, sender)
            topic_id = best_topic_id
        else:
            topic_id = _topic_id(keywords)
            self._create_topic(group_id, topic_id, keywords, sender, content)

        self.cool_down(group_id, current_time=msg_time)

        return topic_id

    def detect_and_update_batch(self, group_id: str,
                                messages: List[dict]) -> Optional[str]:
        """Process multiple messages for topic detection, cool down once.

        More efficient than calling detect_and_update() per message because
        time-based decay is applied only once for the whole batch.

        Args:
            group_id: The group ID.
            messages: List of dicts with 'content', 'sender_name', 'timestamp'.

        Returns:
            The current top topic_id after processing, or None.
        """
        if not messages:
            return None

        last_topic_id = None
        # Process each message for topic matching/creation (without decay)
        for msg in messages:
            content = msg.get('content', '')
            sender = msg.get('sender_name', '')

            keywords = extract_keywords(content)
            if not keywords:
                continue

            kw_set = set(keywords)
            best_topic_id = None
            best_score = 0.0

            topics_key = self._topics_key(group_id)
            existing_topics = self._redis.zrangebyscore(
                topics_key, HEAT_THRESHOLD, '+inf')

            for tid in existing_topics:
                detail_key = self._topic_detail_key(group_id, tid)
                stored_kw_raw = self._redis.hget(detail_key, 'keywords')
                if not stored_kw_raw:
                    continue
                try:
                    stored_kw = set(json.loads(stored_kw_raw))
                except (json.JSONDecodeError, TypeError):
                    continue
                sim = _jaccard(kw_set, stored_kw)
                if sim > best_score:
                    best_score = sim
                    best_topic_id = tid

            if best_score >= JACCARD_THRESHOLD and best_topic_id:
                self._boost_topic(group_id, best_topic_id, keywords, sender)
                last_topic_id = best_topic_id
            else:
                tid = _topic_id(keywords)
                self._create_topic(group_id, tid, keywords, sender, content)
                last_topic_id = tid

        # Apply time-based decay once, using the latest message's timestamp
        latest_ts = max((m.get('timestamp', 0) for m in messages), default=0)
        self.cool_down(group_id, current_time=latest_ts or time.time())

        return last_topic_id

    def get_current_topic(self, group_id: str) -> Optional[Dict]:
        """Return the highest-heat topic as a dict, or None."""
        topics_key = self._topics_key(group_id)

        # Get top topic by score (heat)
        top = self._redis.zrevrange(topics_key, 0, 0, withscores=True)
        if not top:
            return None

        topic_id, heat = top[0]
        if heat < HEAT_THRESHOLD:
            return None

        detail_key = self._topic_detail_key(group_id, topic_id)
        data = self._redis.hgetall(detail_key)
        if not data:
            return None

        keywords = []
        try:
            keywords = json.loads(data.get('keywords', '[]'))
        except (json.JSONDecodeError, TypeError):
            pass

        participants = []
        try:
            participants = json.loads(data.get('participants', '[]'))
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            'topic_id': topic_id,
            'keywords': keywords,
            'participants': participants,
            'summary': data.get('summary', ''),
            'state': data.get('state', 'active'),
            'heat': heat,
        }

    def cool_down(self, group_id: str, current_time: float = None):
        """Decay all topic heats based on elapsed real time.

        Uses an exponential half-life model: heat is halved every
        DECAY_HALF_LIFE seconds.  Rapid successive calls (e.g. batch
        ingest of 20 messages within 1 second) result in negligible
        decay, which is the correct behaviour.
        """
        if current_time is None:
            current_time = time.time()

        last_key = f'ts:{group_id}:last_decay'
        last_raw = self._redis.get(last_key)
        last_decay_at = float(last_raw) if last_raw else current_time

        elapsed = current_time - last_decay_at
        if elapsed < 1.0:
            # Sub-second gap → skip (covers rapid batch calls)
            self._redis.set(last_key, str(current_time), ex=TOPIC_TTL)
            return

        decay_factor = math.pow(0.5, elapsed / DECAY_HALF_LIFE)

        topics_key = self._topics_key(group_id)
        all_topics = self._redis.zrangebyscore(
            topics_key, '-inf', '+inf', withscores=True)

        if not all_topics:
            self._redis.set(last_key, str(current_time), ex=TOPIC_TTL)
            return

        pipe = self._redis.pipeline()
        alive_count = 0
        for tid, heat in all_topics:
            new_heat = heat * decay_factor
            if new_heat < HEAT_THRESHOLD:
                pipe.zrem(topics_key, tid)
                pipe.delete(self._topic_detail_key(group_id, tid))
            else:
                pipe.zadd(topics_key, {tid: new_heat})
                alive_count += 1

        if alive_count > MAX_TOPICS:
            pipe.zremrangebyrank(topics_key, 0, alive_count - MAX_TOPICS - 1)

        pipe.set(last_key, str(current_time), ex=TOPIC_TTL)
        pipe.execute()

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    def _create_topic(self, group_id: str, topic_id: str,
                      keywords: List[str], sender: str, content: str):
        """Create a new topic entry."""
        topics_key = self._topics_key(group_id)
        detail_key = self._topic_detail_key(group_id, topic_id)

        pipe = self._redis.pipeline()
        pipe.zadd(topics_key, {topic_id: INITIAL_HEAT})
        pipe.expire(topics_key, TOPIC_TTL)
        pipe.hset(detail_key, mapping={
            'keywords': json.dumps(keywords, ensure_ascii=False),
            'participants': json.dumps([sender] if sender else [],
                                       ensure_ascii=False),
            'summary': content[:200],
            'state': 'active',
        })
        pipe.expire(detail_key, TOPIC_TTL)
        pipe.execute()

    def _boost_topic(self, group_id: str, topic_id: str,
                     new_keywords: List[str], sender: str):
        """Boost heat and update an existing topic."""
        topics_key = self._topics_key(group_id)
        detail_key = self._topic_detail_key(group_id, topic_id)

        # Boost heat (capped)
        current_heat = self._redis.zscore(topics_key, topic_id) or 0
        new_heat = min(current_heat + HEAT_BOOST, HEAT_CAP)

        # Merge keywords
        stored_kw_raw = self._redis.hget(detail_key, 'keywords') or '[]'
        try:
            stored_kw = json.loads(stored_kw_raw)
        except (json.JSONDecodeError, TypeError):
            stored_kw = []
        merged_kw = list(set(stored_kw + new_keywords))

        # Merge participants
        stored_parts_raw = self._redis.hget(detail_key, 'participants') or '[]'
        try:
            stored_parts = json.loads(stored_parts_raw)
        except (json.JSONDecodeError, TypeError):
            stored_parts = []
        if sender and sender not in stored_parts:
            stored_parts.append(sender)

        pipe = self._redis.pipeline()
        pipe.zadd(topics_key, {topic_id: new_heat})
        pipe.expire(topics_key, TOPIC_TTL)
        pipe.hset(detail_key, mapping={
            'keywords': json.dumps(merged_kw, ensure_ascii=False),
            'participants': json.dumps(stored_parts, ensure_ascii=False),
        })
        pipe.expire(detail_key, TOPIC_TTL)
        pipe.execute()
