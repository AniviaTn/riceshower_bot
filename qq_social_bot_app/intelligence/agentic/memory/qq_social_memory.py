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
import asyncio
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
    _async_inited: bool = False

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def _ensure_init(self):
        """Lazy-initialise internal components (sync parts only)."""
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

        self._working_memory = WorkingMemory(redis_url=self.redis_url)
        # TopicStateManager shares the async Redis client
        self._topic_manager = TopicStateManager(self._working_memory._redis)

    async def _async_ensure_init(self):
        """Lazy-initialise + verify async Redis connectivity."""
        self._ensure_init()
        if self._async_inited:
            return

        try:
            if not await self._working_memory.ping():
                logger.warning('Redis not reachable at %s, WorkingMemory disabled',
                               self.redis_url)
                self._working_memory = None
                self._topic_manager = None
        except Exception:
            logger.warning('Failed to connect to Redis, WorkingMemory disabled')
            self._working_memory = None
            self._topic_manager = None

        self._async_inited = True

    # ==============================================================
    # GroupMessage-aware write (sync – kept for backward compat)
    # ==============================================================

    def add_group_message(self, msg: GroupMessage):
        """Write a GroupMessage into WorkingMemory (Layer 1), raw file store, and update topics.

        NOTE: This is the sync backward-compat method. Prefer async_add_group_message().
        """
        raise NotImplementedError(
            'Sync add_group_message() is no longer supported after async conversion. '
            'Use await async_add_group_message() instead.')

    def add_group_messages(self, msgs: List[GroupMessage]):
        """Sync backward-compat stub. Use async_add_group_messages() instead."""
        raise NotImplementedError(
            'Sync add_group_messages() is no longer supported after async conversion. '
            'Use await async_add_group_messages() instead.')

    # ==============================================================
    # GroupMessage-aware write (async)
    # ==============================================================

    async def async_add_group_message(self, msg: GroupMessage):
        """Write a GroupMessage into WorkingMemory (Layer 1), raw file store, and update topics."""
        await self._async_ensure_init()
        if self._working_memory:
            await self._working_memory.add_message(msg.group_id, msg)
        if self._raw_store:
            try:
                await self._raw_store.async_append(msg)
            except Exception:
                logger.debug('Raw message file write failed', exc_info=True)
        if self._topic_manager:
            try:
                await self._topic_manager.detect_and_update(
                    msg.group_id, msg.to_dict())
            except Exception:
                logger.debug('Topic detection failed', exc_info=True)

    async def async_add_group_messages(self, msgs: List[GroupMessage]):
        """Batch-write multiple GroupMessages into WorkingMemory.

        More efficient than calling async_add_group_message() in a loop:
        uses a single Redis pipeline and runs topic detection with
        batch-aware decay (cool down only once).
        """
        if not msgs:
            return
        await self._async_ensure_init()

        # Group messages by group_id (usually all the same group)
        by_group: dict[str, list] = {}
        for msg in msgs:
            by_group.setdefault(msg.group_id, []).append(msg)

        for group_id, group_msgs in by_group.items():
            if self._working_memory:
                await self._working_memory.add_messages(group_id, group_msgs)
            if self._raw_store:
                try:
                    await self._raw_store.async_append_batch(group_msgs)
                except Exception:
                    logger.debug('Raw message batch file write failed', exc_info=True)
            if self._topic_manager:
                try:
                    await self._topic_manager.detect_and_update_batch(
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
        # The framework calls this after each turn. In async mode, we cannot
        # call async Redis here, so we skip WM write. The agent's async_execute
        # handles WM writes directly.
        pass

    def get(self, session_id: str = None, agent_id: str = None,
            prune: bool = False, token_budget: int = None,
            **kwargs) -> List[Message]:
        """Retrieve recent messages from WorkingMemory as Message objects.

        NOTE: In the async pipeline, context is built via async_build_context().
        This sync method is kept for framework compatibility but returns empty.
        """
        return []

    # ==============================================================
    # build_context - THE primary interface (async)
    # ==============================================================

    async def async_build_context(self, session_id: str, agent_id: str = None,
                                  token_budget: int = None, **kwargs) -> dict:
        """Assemble four-layer social context for prompt injection (async).

        Returns:
            {
                'chat_history': recent conversation text,
                'social_context': user card + group profile + episodes (filtered),
                'current_topic': str,
                'current_mood': str,
                'persona_context': str,
            }
        """
        await self._async_ensure_init()
        group_id = kwargs.get('group_id', '') or session_id or ''
        user_id = kwargs.get('user_id', '')

        # Layer 1: WorkingMemory (async Redis)
        if self._working_memory and group_id:
            wm_ctx = await self._working_memory.get_context(group_id)
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
                topic_data = await self._topic_manager.get_current_topic(group_id)
                if topic_data and topic_data.get('keywords'):
                    current_topic = ', '.join(topic_data['keywords'])
            except Exception:
                logger.debug('TopicStateManager lookup failed', exc_info=True)
        if not current_topic:
            current_topic = wm_ctx.get('current_topic', '')

        # Layers 2/3/4: SQLite (wrapped with asyncio.to_thread)
        trigger_user_ids = kwargs.get('trigger_user_ids', [])
        if not trigger_user_ids and user_id:
            trigger_user_ids = [user_id]

        def _build_sqlite_context():
            user_context_parts = []
            for uid in trigger_user_ids:
                uc = self._service.build_user_context(uid, group_id)
                if uc:
                    user_context_parts.append(uc)
            user_context = '\n\n'.join(user_context_parts)

            group_context = ''
            if group_id:
                group_context = self._service.build_group_context(group_id)

            episodes_raw = []
            episode_context = ''
            if group_id:
                episodes_raw = self._service.get_recent_episodes(group_id)
                episode_context = self._service.build_episode_context(group_id)

            persona_context = ''
            if group_id:
                try:
                    persona_context = self._service.build_persona_context(group_id)
                except Exception:
                    logger.debug('Persona context build failed', exc_info=True)

            return user_context, group_context, episode_context, episodes_raw, persona_context

        (user_context, group_context, episode_context,
         episodes_raw, persona_context) = await asyncio.to_thread(_build_sqlite_context)

        # Safety filter (CPU-only, fast)
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

    def build_context(self, session_id: str, agent_id: str = None,
                      token_budget: int = None, **kwargs) -> dict:
        """Sync backward-compat stub. Use async_build_context() instead."""
        raise NotImplementedError(
            'Sync build_context() is no longer supported after async conversion. '
            'Use await async_build_context() instead.')

    # ==============================================================
    # Post-turn extraction (async)
    # ==============================================================

    async def async_extract_and_store_candidates_from_context(
        self, group_id: str, bot_response: str = '',
        max_messages: int = 100,
    ) -> None:
        """Extract candidate memories from the full recent conversation (async).

        Reads the last *max_messages* from WorkingMemory (multi-user, multi-turn)
        so the extractor can discover group-level and cross-user information.
        """
        await self._async_ensure_init()
        if not group_id:
            return

        # Pull recent messages from WorkingMemory (async Redis)
        messages: List[dict] = []
        if self._working_memory:
            raw_msgs = await self._working_memory.get_recent_messages(
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

        # Gather existing profiles for context (SQLite → to_thread)
        def _get_profiles():
            existing = {}
            for m in messages:
                uid = m.get('user_id', '')
                if uid and uid not in existing and uid != 'bot_self':
                    profile = self._service.get_user_profile(uid)
                    if profile:
                        existing[uid] = profile
            return existing

        existing = await asyncio.to_thread(_get_profiles)

        # Async LLM extraction
        candidates = await self._extractor.async_extract_candidates(
            messages, group_id, existing_profiles=existing)

        # Store candidates (SQLite → to_thread)
        def _store_candidates():
            for c in candidates:
                try:
                    self._service.add_candidate(c)
                except Exception:
                    logger.exception('Failed to store candidate memory')

        if candidates:
            await asyncio.to_thread(_store_candidates)

    async def async_run_consolidation(self):
        """Promote eligible candidates to long-term memory (async)."""
        await self._async_ensure_init()
        await self._extractor.async_consolidate_candidates(self._service)

    # ==============================================================
    # Legacy sync stubs
    # ==============================================================

    def extract_and_store_candidates_from_context(
        self, group_id: str, bot_response: str = '',
        max_messages: int = 100,
    ) -> None:
        """Sync stub. Use async_extract_and_store_candidates_from_context() instead."""
        raise NotImplementedError(
            'Sync extract_and_store_candidates_from_context() is no longer supported. '
            'Use the async variant instead.')

    def run_consolidation(self):
        """Sync stub. Use async_run_consolidation() instead."""
        raise NotImplementedError(
            'Sync run_consolidation() is no longer supported. '
            'Use await async_run_consolidation() instead.')

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
