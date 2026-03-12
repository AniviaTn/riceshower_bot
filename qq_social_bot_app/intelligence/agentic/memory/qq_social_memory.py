"""QQSocialMemory: bridges agentUniverse Memory ↔ Markdown + ChromaDB memory.

New architecture:
    - Static persona: Markdown files in persona/ (cached)
    - Dynamic profiles: people/{id}/profile.md, groups/{id}/profile.md (auto-loaded)
    - Indexed memories: all other .md files (searchable via tools)
    - WorkingMemory (Redis): real-time messages, topic, mood
    - ID mapping: QQ号 ↔ display name replacement
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
from qq_social_bot_app.intelligence.social_memory.topic_state import (
    TopicStateManager,
)
from qq_social_bot_app.intelligence.social_memory.raw_message_store import (
    RawMessageStore,
)
from qq_social_bot_app.intelligence.social_memory.memory_services import (
    init_services, get_id_mapping_service, get_markdown_service,
    get_embedding_service,
)
from qq_social_bot_app.intelligence.utils import bot_config

logger = logging.getLogger(__name__)


class QQSocialMemory(Memory):
    """Bridges agentUniverse Memory ↔ Markdown/ChromaDB memory system."""

    data_root: str = os.path.expanduser('~/.qq_bot_data')
    redis_url: str = 'redis://localhost:6379/0'

    # Internal components (not serialised)
    _working_memory: Optional[WorkingMemory] = None
    _topic_manager: Optional[TopicStateManager] = None
    _raw_store: Optional[RawMessageStore] = None
    _async_inited: bool = False
    _persona_cache: Optional[str] = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def _ensure_init(self):
        """Lazy-initialise internal components (sync parts only)."""
        if self._raw_store is not None:
            return

        # Fall back to centralised config if YAML didn't override
        if not self.data_root or self.data_root == os.path.expanduser('~/.qq_bot_data'):
            self.data_root = bot_config.get_data_root()
        if not self.redis_url or self.redis_url == 'redis://localhost:6379/0':
            self.redis_url = bot_config.get_redis_url()

        # Initialise singleton memory services
        init_services(data_root=self.data_root)

        self._raw_store = RawMessageStore(
            base_dir=os.path.join(self.data_root, 'raw_messages'))
        self._working_memory = WorkingMemory(redis_url=self.redis_url)
        self._topic_manager = TopicStateManager(self._working_memory._redis)

        # Try reindex on first init
        try:
            get_embedding_service().reindex_all()
        except Exception:
            logger.debug('Embedding reindex failed (may be normal on first run)',
                         exc_info=True)

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
    # Cache management
    # ==============================================================

    def invalidate_persona_cache(self) -> None:
        """Clear the persona cache so the next build_context reloads from disk."""
        self._persona_cache = None

    # ==============================================================
    # GroupMessage-aware write (sync stubs)
    # ==============================================================

    def add_group_message(self, msg: GroupMessage):
        raise NotImplementedError('Use await async_add_group_message() instead.')

    def add_group_messages(self, msgs: List[GroupMessage]):
        raise NotImplementedError('Use await async_add_group_messages() instead.')

    # ==============================================================
    # GroupMessage-aware write (async)
    # ==============================================================

    async def async_add_group_message(self, msg: GroupMessage):
        """Write a GroupMessage into WorkingMemory + raw store + topic detection."""
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
        """Batch-write multiple GroupMessages."""
        if not msgs:
            return
        await self._async_ensure_init()

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
        pass

    def get(self, session_id: str = None, agent_id: str = None,
            prune: bool = False, token_budget: int = None,
            **kwargs) -> List[Message]:
        return []

    # ==============================================================
    # build_context - THE primary interface (async)
    # ==============================================================

    async def async_build_context(self, session_id: str, agent_id: str = None,
                                  token_budget: int = None, **kwargs) -> dict:
        """Assemble context from Markdown files + Redis working memory.

        Returns:
            {
                'core_persona': static persona text,
                'chat_history': recent conversation text,
                'social_context': profiles + recent events,
                'current_topic': str,
                'current_mood': str,
                'current_time': str,
            }
        """
        await self._async_ensure_init()
        group_id = kwargs.get('group_id', '') or session_id or ''
        trigger_user_ids = kwargs.get('trigger_user_ids', [])
        user_id = kwargs.get('user_id', '')
        if not trigger_user_ids and user_id:
            trigger_user_ids = [user_id]

        # 1. Static persona (cached)
        core_persona = await self._get_persona_cached()

        # 2-4. Dynamic context from Markdown files (sync → to_thread)
        social_context = await asyncio.to_thread(
            self._build_social_context, group_id, trigger_user_ids)

        # 5. WorkingMemory from Redis (with live name resolution)
        id_mapping = get_id_mapping_service()
        if self._working_memory and group_id:
            wm_ctx = await self._working_memory.get_context(
                group_id, id_mapping=id_mapping)
        else:
            wm_ctx = {
                'recent_messages_text': '',
                'current_topic': '',
                'mood': '',
                'participants': [],
            }

        chat_history = wm_ctx.get('recent_messages_text', '')
        recent_messages = wm_ctx.get('recent_messages', [])
        current_mood = wm_ctx.get('mood', '')

        # Current topic: prefer TopicStateManager
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

        # 7. Current time
        now = time.localtime()
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        current_time = (
            f'{now.tm_year}年{now.tm_mon}月{now.tm_mday}日 '
            f'{time.strftime("%H:%M", now)} {weekdays[now.tm_wday]}'
        )

        return {
            'core_persona': core_persona,
            'chat_history': chat_history,
            'recent_messages': recent_messages,
            'social_context': social_context,
            'current_topic': current_topic,
            'current_mood': current_mood,
            'current_time': current_time,
        }

    def build_context(self, session_id: str, agent_id: str = None,
                      token_budget: int = None, **kwargs) -> dict:
        raise NotImplementedError(
            'Use await async_build_context() instead.')

    # ==============================================================
    # Internal context builders
    # ==============================================================

    async def _get_persona_cached(self) -> str:
        """Read and cache persona files."""
        if self._persona_cache is not None:
            return self._persona_cache

        def _read():
            md = get_markdown_service()
            parts = []
            core = md.read_file('persona/core_persona.md')
            if core:
                parts.append(core.strip())
            ltm = md.read_file('persona/long_term_memory.md')
            if ltm:
                parts.append(ltm.strip())
            return '\n\n'.join(parts)

        self._persona_cache = await asyncio.to_thread(_read)
        return self._persona_cache

    def _build_social_context(self, group_id: str,
                              trigger_user_ids: list[str]) -> str:
        """Build social context string from Markdown profile files + recent events.

        Runs synchronously (called via to_thread).
        """
        md = get_markdown_service()
        id_map = get_id_mapping_service()
        parts = []

        # Group profile
        if group_id:
            group_profile = md.read_file(f'groups/{group_id}/profile.md')
            group_name = id_map.get_group_name(group_id) or group_id
            if group_profile:
                parts.append(f'## 当前群: {group_name}\n{group_profile.strip()}')
            else:
                parts.append(f'## 当前群: {group_name}')

        # User profiles
        for uid in trigger_user_ids:
            user_profile = md.read_file(f'people/{uid}/profile.md')
            user_name = id_map.get_user_name(uid) or uid
            if user_profile:
                parts.append(f'## 成员: {user_name}\n{user_profile.strip()}')

        # Recent events: group (last 3) + each user (last 2)
        recent_parts = []
        if group_id:
            group_recent = md.get_recent_files(f'groups/{group_id}', limit=3)
            for path in group_recent:
                content = md.read_file(path)
                if content:
                    fname = os.path.basename(path)
                    recent_parts.append(f'### {fname}\n{content.strip()}')

        for uid in trigger_user_ids:
            user_recent = md.get_recent_files(f'people/{uid}', limit=2)
            for path in user_recent:
                content = md.read_file(path)
                if content:
                    fname = os.path.basename(path)
                    user_name = id_map.get_user_name(uid) or uid
                    recent_parts.append(
                        f'### [{user_name}] {fname}\n{content.strip()}')

        if recent_parts:
            parts.append('## 近期记忆\n' + '\n\n'.join(recent_parts))

        return '\n\n'.join(parts)

    # ==============================================================
    # Configuration
    # ==============================================================

    def initialize_by_component_configer(self,
                                         component_configer: MemoryConfiger) -> 'QQSocialMemory':
        """Read YAML config and apply to this instance."""
        super().initialize_by_component_configer(component_configer)

        raw = {}
        if component_configer.configer and hasattr(component_configer.configer, 'value'):
            raw = component_configer.configer.value or {}

        if 'data_root' in raw:
            self.data_root = os.path.expanduser(raw['data_root'])
        if 'redis_url' in raw:
            self.redis_url = raw['redis_url']

        return self
