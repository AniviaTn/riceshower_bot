"""QQSocialMemory: bridges the agentUniverse Memory interface with the
four-layer social memory system.

Layers:
    L1 WorkingMemory  (Redis)  - recent messages, topic, mood, participants
    L2 UserProfile + Relationship (SQLite) - per-user traits and relationship scores
    L3 GroupProfile   (SQLite) - group culture, memes, taboos
    L4 EpisodeMemory  (SQLite) - shared experiences and events
    Buffer: CandidateMemory (SQLite) - pending memory candidates

The build_context() method assembles all layers into prompt-injectable variables.
"""
import logging
import os
import time
from typing import List, Optional

from pydantic import ConfigDict

from agentuniverse.agent.memory.memory import Memory
from agentuniverse.agent.memory.message import Message
from agentuniverse.base.config.component_configer.configers.memory_configer import (
    MemoryConfiger,
)

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.working_memory import WorkingMemory
from qq_social_bot_app.intelligence.social_memory.social_memory_service import (
    SocialMemoryService,
)
from qq_social_bot_app.intelligence.social_memory.memory_extractor import (
    MemoryExtractor,
)
from qq_social_bot_app.intelligence.social_memory.social_safety_filter import (
    SocialSafetyFilter,
)
from qq_social_bot_app.intelligence.social_memory.topic_state import (
    TopicStateManager,
)
from qq_social_bot_app.intelligence.social_memory.raw_message_store import (
    RawMessageStore,
)

logger = logging.getLogger(__name__)


class QQSocialMemory(Memory):
    """Bridges agentUniverse Memory ↔ four-layer social memory system."""

    db_path: str = 'data/qq_social.db'
    redis_url: str = 'redis://localhost:6379/0'
    extractor_llm_name: str = 'social_extractor_llm'
    raw_message_dir: str = 'data/raw_messages'

    # Internal components (not serialised)
    _working_memory: Optional[WorkingMemory] = None
    _service: Optional[SocialMemoryService] = None
    _extractor: Optional[MemoryExtractor] = None
    _filter: Optional[SocialSafetyFilter] = None
    _topic_manager: Optional[TopicStateManager] = None
    _raw_store: Optional[RawMessageStore] = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def _ensure_init(self):
        """Lazy-initialise internal components."""
        if self._service is not None:
            return

        # Ensure the data directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._service = SocialMemoryService(self.db_path)
        self._extractor = MemoryExtractor(llm_name=self.extractor_llm_name)
        self._filter = SocialSafetyFilter()
        # RawMessageStore uses its own absolute default; only override if
        # the config value looks like an explicit absolute path.
        if os.path.isabs(self.raw_message_dir):
            self._raw_store = RawMessageStore(base_dir=self.raw_message_dir)
        else:
            # Relative path in YAML → let RawMessageStore use its built-in
            # absolute default anchored to the app root.
            self._raw_store = RawMessageStore()

        try:
            self._working_memory = WorkingMemory(redis_url=self.redis_url)
            if not self._working_memory.ping():
                logger.warning('Redis not reachable at %s, WorkingMemory disabled',
                               self.redis_url)
                self._working_memory = None
            else:
                # Share the Redis client with TopicStateManager
                self._topic_manager = TopicStateManager(
                    self._working_memory._redis)
        except Exception:
            logger.warning('Failed to connect to Redis, WorkingMemory disabled')
            self._working_memory = None

    # ==============================================================
    # GroupMessage-aware write (called by the Agent)
    # ==============================================================

    def add_group_message(self, msg: GroupMessage):
        """Write a GroupMessage into WorkingMemory (Layer 1), raw file store, and update topics."""
        self._ensure_init()
        if self._working_memory:
            self._working_memory.add_message(msg.group_id, msg)
        if self._raw_store:
            try:
                self._raw_store.append(msg)
            except Exception:
                logger.debug('Raw message file write failed', exc_info=True)
        if self._topic_manager:
            try:
                self._topic_manager.detect_and_update(
                    msg.group_id, msg.to_dict())
            except Exception:
                logger.debug('Topic detection failed', exc_info=True)

    def add_group_messages(self, msgs: List[GroupMessage]):
        """Batch-write multiple GroupMessages into WorkingMemory.

        More efficient than calling add_group_message() in a loop:
        uses a single Redis pipeline and runs topic detection with
        batch-aware decay (cool down only once).
        """
        if not msgs:
            return
        self._ensure_init()

        # Group messages by group_id (usually all the same group)
        by_group: dict[str, list] = {}
        for msg in msgs:
            by_group.setdefault(msg.group_id, []).append(msg)

        for group_id, group_msgs in by_group.items():
            if self._working_memory:
                self._working_memory.add_messages(group_id, group_msgs)
            if self._raw_store:
                try:
                    self._raw_store.append_batch(group_msgs)
                except Exception:
                    logger.debug('Raw message batch file write failed', exc_info=True)
            if self._topic_manager:
                try:
                    self._topic_manager.detect_and_update_batch(
                        group_id,
                        [m.to_dict() for m in group_msgs])
                except Exception:
                    logger.debug('Batch topic detection failed', exc_info=True)

    # ==============================================================
    # Framework Memory interface
    # ==============================================================

    def add(self, message_list: List[Message], session_id: str = None,
            agent_id: str = None, **kwargs) -> None:
        """Standard Memory.add() - stores messages in WorkingMemory as well."""
        self._ensure_init()
        # The framework calls this with Message objects after each turn.
        # We store them in WorkingMemory if group_id is available.
        group_id = kwargs.get('group_id') or session_id or ''
        if self._working_memory and group_id:
            for msg in message_list:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # msg.type may be an enum (has .value) or a plain string
                if msg.type is None:
                    role = 'unknown'
                elif hasattr(msg.type, 'value'):
                    role = str(msg.type.value)
                else:
                    role = str(msg.type)
                gm = GroupMessage(
                    content=content,
                    sender_id=role,
                    sender_name=role,
                    group_id=group_id,
                )
                self._working_memory.add_message(group_id, gm)

    def get(self, session_id: str = None, agent_id: str = None,
            prune: bool = False, token_budget: int = None,
            **kwargs) -> List[Message]:
        """Retrieve recent messages from WorkingMemory as Message objects."""
        self._ensure_init()
        group_id = kwargs.get('group_id') or session_id or ''
        if not self._working_memory or not group_id:
            return []
        raw_msgs = self._working_memory.get_recent_messages(group_id)
        messages = []
        for m in raw_msgs:
            from agentuniverse.agent.memory.enum import ChatMessageEnum
            role = ChatMessageEnum.HUMAN
            messages.append(Message(
                type=role,
                content=f'{m.get("sender_name", "")}: {m.get("content", "")}',
            ))
        return messages

    # ==============================================================
    # build_context - THE primary interface
    # ==============================================================

    def build_context(self, session_id: str, agent_id: str = None,
                      token_budget: int = None, **kwargs) -> dict:
        """Assemble four-layer social context for prompt injection.

        Returns:
            {
                'chat_history': recent conversation text,
                'social_context': user card + group profile + episodes (filtered),
                'current_topic': str,
                'current_mood': str,
                'persona_context': str,
            }
        """
        self._ensure_init()
        group_id = kwargs.get('group_id', '') or session_id or ''
        user_id = kwargs.get('user_id', '')

        # Layer 1: WorkingMemory
        if self._working_memory and group_id:
            wm_ctx = self._working_memory.get_context(group_id)
        else:
            wm_ctx = {
                'recent_messages_text': '',
                'current_topic': '',
                'mood': '',
                'participants': [],
            }

        chat_history = wm_ctx.get('recent_messages_text', '')
        current_mood = wm_ctx.get('mood', '')

        # Current topic: prefer TopicStateManager, fallback to WM manual topic
        current_topic = ''
        if self._topic_manager and group_id:
            try:
                topic_data = self._topic_manager.get_current_topic(group_id)
                if topic_data and topic_data.get('keywords'):
                    current_topic = ', '.join(topic_data['keywords'])
            except Exception:
                logger.debug('TopicStateManager lookup failed', exc_info=True)
        if not current_topic:
            current_topic = wm_ctx.get('current_topic', '')

        # Layers 2/3/4: SQLite
        trigger_user_ids = kwargs.get('trigger_user_ids', [])
        if not trigger_user_ids and user_id:
            trigger_user_ids = [user_id]
        user_context_parts = []
        for uid in trigger_user_ids:
            uc = self._service.build_user_context(uid, group_id)
            if uc:
                user_context_parts.append(uc)
        user_context = '\n\n'.join(user_context_parts)

        group_context = ''
        if group_id:
            group_context = self._service.build_group_context(group_id)

        # Get raw episodes for sensitivity-aware filtering
        episodes_raw = []
        episode_context = ''
        if group_id:
            episodes_raw = self._service.get_recent_episodes(group_id)
            episode_context = self._service.build_episode_context(group_id)

        # Safety filter with raw episodes for sensitivity-based filtering
        filtered = self._filter.filter_context(
            user_context, group_context, episode_context,
            episodes_raw=episodes_raw if episodes_raw else None)

        # Assemble social_context block
        social_parts = []
        if filtered.get('safety_note'):
            social_parts.append(filtered['safety_note'])
        if filtered.get('user_context'):
            social_parts.append(filtered['user_context'])
        if filtered.get('group_context'):
            social_parts.append(filtered['group_context'])
        if filtered.get('episode_context'):
            social_parts.append(filtered['episode_context'])

        social_context = '\n\n'.join(social_parts) if social_parts else ''

        # Layer 5: Persona context
        persona_context = ''
        if group_id:
            try:
                persona_context = self._service.build_persona_context(group_id)
            except Exception:
                logger.debug('Persona context build failed', exc_info=True)

        # Current time for agent awareness
        now = time.localtime()
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        current_time = (
            f'{now.tm_year}年{now.tm_mon}月{now.tm_mday}日 '
            f'{time.strftime("%H:%M", now)} {weekdays[now.tm_wday]}'
        )

        return {
            'chat_history': chat_history,
            'social_context': social_context,
            'current_topic': current_topic,
            'current_mood': current_mood,
            'persona_context': persona_context,
            'current_time': current_time,
        }

    # ==============================================================
    # Post-turn extraction
    # ==============================================================

    def extract_and_store_candidates_from_context(
        self, group_id: str, bot_response: str = '',
        max_messages: int = 100,
    ) -> None:
        """Extract candidate memories from the full recent conversation.

        Reads the last *max_messages* from WorkingMemory (multi-user, multi-turn)
        so the extractor can discover group-level and cross-user information.
        """
        self._ensure_init()
        if not group_id:
            return

        # Pull recent messages from WorkingMemory
        messages: List[dict] = []
        if self._working_memory:
            raw_msgs = self._working_memory.get_recent_messages(
                group_id, limit=max_messages)
            for m in raw_msgs:
                messages.append({
                    'role': 'assistant' if m.get('sender_id') == 'bot_self' else 'user',
                    'user_id': m.get('sender_id', ''),
                    'user_name': m.get('sender_name', ''),
                    'content': m.get('content', ''),
                })

        if not messages:
            return

        # Gather existing profiles for context
        existing = {}
        for m in messages:
            uid = m.get('user_id', '')
            if uid and uid not in existing and uid != 'bot_self':
                profile = self._service.get_user_profile(uid)
                if profile:
                    existing[uid] = profile

        candidates = self._extractor.extract_candidates(
            messages, group_id, existing_profiles=existing)

        for c in candidates:
            try:
                self._service.add_candidate(c)
            except Exception:
                logger.exception('Failed to store candidate memory')

    def extract_and_store_candidates(self, messages: List[dict], group_id: str):
        """Legacy method – extracts from an explicit message list.

        Prefer extract_and_store_candidates_from_context() which reads the
        full conversation from WorkingMemory automatically.
        """
        self._ensure_init()
        if not messages or not group_id:
            return

        existing = {}
        for m in messages:
            uid = m.get('user_id', '')
            if uid and uid not in existing:
                profile = self._service.get_user_profile(uid)
                if profile:
                    existing[uid] = profile

        candidates = self._extractor.extract_candidates(
            messages, group_id, existing_profiles=existing)

        for c in candidates:
            try:
                self._service.add_candidate(c)
            except Exception:
                logger.exception('Failed to store candidate memory')

    def run_consolidation(self):
        """Promote eligible candidates to long-term memory."""
        self._ensure_init()
        self._extractor.consolidate_candidates(self._service)

    # ==============================================================
    # Configuration
    # ==============================================================

    def initialize_by_component_configer(self,
                                         component_configer: MemoryConfiger) -> 'QQSocialMemory':
        """Read YAML config and apply to this instance."""
        super().initialize_by_component_configer(component_configer)

        # Read custom fields from the raw YAML config dict
        raw = {}
        if component_configer.configer and hasattr(component_configer.configer, 'value'):
            raw = component_configer.configer.value or {}

        if 'db_path' in raw:
            self.db_path = raw['db_path']
        if 'redis_url' in raw:
            self.redis_url = raw['redis_url']
        if 'extractor_llm_name' in raw:
            self.extractor_llm_name = raw['extractor_llm_name']
        if 'raw_message_dir' in raw:
            self.raw_message_dir = raw['raw_message_dir']

        return self
