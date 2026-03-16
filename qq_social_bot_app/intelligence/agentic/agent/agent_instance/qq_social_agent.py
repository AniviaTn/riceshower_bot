"""QQ group chat social Agent.

External API is only agent.run() / agent.async_run():

  output = await agent.async_run(messages=[msg], bot_id='bot_self', bot_names=['小U'], must_reply=True)
  output = await agent.async_run(messages=[msg1, msg2, ...], bot_id='bot_self', bot_names=['小U'], must_reply=False)

  response = output.get_data('output', '')
"""
import base64
import logging
import os
import time

from agentuniverse.agent.input_object import InputObject
from agentuniverse.agent.memory.enum import ChatMessageEnum
from agentuniverse.agent.memory.memory import Memory
from agentuniverse.agent.memory.message import Message, ToolCall
from agentuniverse.agent.template.agent_template import AgentTemplate
from agentuniverse.llm.llm import LLM
from agentuniverse.llm.llm_output import LLMOutput
from agentuniverse.ai_context.agent_context import AgentContext

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.working_memory import (
    format_message_time,
)
from qq_social_bot_app.intelligence.utils import bot_config

logger = logging.getLogger(__name__)

BOT_SENDER_ID = 'bot_self'

# Max images embedded as vision content (limits token cost).
_MAX_IMAGES = 10
# Only attempt image download for the most recent N messages.
_IMAGE_DOWNLOAD_SCOPE = 30


# ------------------------------------------------------------------
# Image helpers
# ------------------------------------------------------------------

def _file_to_data_url(path: str) -> str | None:
    """Convert a local image file to a ``data:`` base64 URL."""
    ext = os.path.splitext(path)[1].lower()
    mime_map = {'.png': 'image/png', '.jpeg': 'image/jpeg',
                '.jpg': 'image/jpeg', '.gif': 'image/gif',
                '.webp': 'image/webp'}
    mime = mime_map.get(ext)
    if not mime:
        return None
    try:
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        return f'data:{mime};base64,{b64}'
    except OSError:
        return None


def _build_multimodal_content(header: str, text: str,
                              image_urls: list[str],
                              url_to_path: dict[str, str],
                              embeddable_urls: set[str]) -> list[dict]:
    """Split *text* at ``[图片]`` placeholders and interleave images.

    Only URLs present in *embeddable_urls* are actually embedded; others
    remain as a ``[图片]`` text placeholder.
    """
    parts = text.split('[图片]')
    blocks: list[dict] = []
    url_idx = 0

    for i, part in enumerate(parts):
        segment = part
        if i == 0:
            segment = header + segment
        if segment:
            blocks.append({'type': 'text', 'text': segment})

        # Insert image after each split except the last part
        if i < len(parts) - 1:
            url = image_urls[url_idx] if url_idx < len(image_urls) else None
            url_idx += 1
            if url and url in embeddable_urls:
                local = url_to_path.get(url)
                data_url = _file_to_data_url(local) if local else None
                if data_url:
                    blocks.append({'type': 'image_url',
                                   'image_url': {'url': data_url}})
                    continue
            # Fallback: keep text placeholder
            blocks.append({'type': 'text', 'text': '[图片]'})

    return blocks or [{'type': 'text', 'text': header}]


class QQSocialAgent(AgentTemplate):
    """QQ group chat social AI assistant."""

    def input_keys(self) -> list[str]:
        return []

    def output_keys(self) -> list[str]:
        return ['output']

    def parse_result(self, agent_result: dict) -> dict:
        return {**agent_result, 'output': agent_result.get('output', '')}

    def parse_input(self, input_object: InputObject, agent_input: dict) -> dict:
        messages = input_object.get_data('messages')
        group_message = input_object.get_data('group_message')

        if messages and isinstance(messages, list) and len(messages) > 0:
            sorted_msgs = sorted(messages, key=lambda m: m.timestamp)
            agent_input['messages'] = sorted_msgs
            agent_input['group_id'] = sorted_msgs[0].group_id
            agent_input['session_id'] = sorted_msgs[0].group_id
            agent_input['input'] = sorted_msgs[-1].content
        elif group_message:
            agent_input['messages'] = [group_message]
            agent_input['group_id'] = group_message.group_id
            agent_input['session_id'] = group_message.group_id
            agent_input['input'] = group_message.content
            agent_input['user_id'] = group_message.sender_id
            agent_input['user_name'] = group_message.sender_name
        else:
            agent_input['messages'] = []
            agent_input['input'] = input_object.get_data('input', '') or ''
            agent_input['session_id'] = input_object.get_data('session_id', '') or ''
            agent_input['group_id'] = input_object.get_data('group_id', '') or ''
            agent_input['user_id'] = input_object.get_data('user_id', '') or ''
            agent_input['user_name'] = input_object.get_data('user_name', '') or ''

        agent_input['bot_id'] = (
            input_object.get_data('bot_id') or BOT_SENDER_ID)
        agent_input['bot_names'] = (
            input_object.get_data('bot_names') or ['bot', 'Bot'])
        agent_input['must_reply'] = (
            input_object.get_data('must_reply', True))

        return agent_input

    # ==============================================================
    # execute – sync stub
    # ==============================================================

    def execute(self, input_object: InputObject, agent_input: dict,
                **kwargs) -> dict:
        raise NotImplementedError(
            'Sync execute() is no longer supported. '
            'Use await agent.async_run() instead.')

    # ==============================================================
    # async_execute – the full async pipeline
    # ==============================================================

    async def async_execute(self, input_object: InputObject, agent_input: dict,
                            **kwargs) -> dict:
        memory: Memory = self.process_memory(agent_input, **kwargs)

        group_id = agent_input.get('group_id', '')
        messages = agent_input.get('messages', [])
        must_reply = agent_input.get('must_reply', True)
        bot_id = agent_input.get('bot_id', BOT_SENDER_ID)

        # Ensure async init
        if hasattr(memory, '_async_ensure_init'):
            await memory._async_ensure_init()

        # ---- Phase 1: Ingest all messages into WorkingMemory ----
        if messages:
            await self._async_ingest_messages(memory, messages)

        # ---- Phase 2: Determine trigger messages ----
        if messages:
            if must_reply:
                primary = messages[-1]
                agent_input['user_id'] = primary.sender_id
                agent_input['user_name'] = primary.sender_name
                agent_input['trigger_user_ids'] = list(
                    dict.fromkeys(m.sender_id for m in messages))
                agent_input['trigger_messages'] = (
                    self._format_trigger_messages(messages))
            else:
                recent = messages[-3:] if len(messages) > 3 else messages
                primary = recent[-1]
                agent_input['user_id'] = primary.sender_id
                agent_input['user_name'] = primary.sender_name
                agent_input['trigger_user_ids'] = list(
                    dict.fromkeys(m.sender_id for m in messages))
                agent_input['trigger_messages'] = (
                    self._format_trigger_messages(recent))
        else:
            agent_input.setdefault('trigger_messages',
                                   agent_input.get('input', ''))

        # ---- Phase 3: Build social context (async) ----
        context = await memory.async_build_context(
            session_id=agent_input.get('session_id', ''),
            group_id=group_id,
            user_id=agent_input.get('user_id', ''),
            trigger_user_ids=agent_input.get('trigger_user_ids', []),
        )
        agent_input.update(context)

        # ---- Phase 3.4: Build individual chat history messages ----
        recent_messages = agent_input.pop('recent_messages', [])
        from qq_social_bot_app.intelligence.social_memory.memory_services import (
            get_id_mapping_service,
        )
        id_mapping = get_id_mapping_service()

        # Look up cached images (Phase E downloaded them when messages arrived)
        url_to_path = self._lookup_history_images(recent_messages)

        chat_messages = self._build_chat_messages(
            recent_messages, bot_id, id_mapping=id_mapping,
            url_to_path=url_to_path, max_images=_MAX_IMAGES,
        )

        num_new = len(messages)
        num_old = max(0, len(recent_messages) - num_new)

        logger.info(
            'Built %d individual chat messages for group %s '
            '(%d old, %d new, %d images downloaded)',
            len(chat_messages), group_id, num_old, num_new,
            len(url_to_path))

        # ---- Phase 4: Run LLM (async) ----
        # Remove keys that should NOT leak into prompt template rendering
        agent_input.pop('chat_history', None)

        llm: LLM = self.process_llm(**kwargs)
        agent_context = self._create_agent_context(
            input_object, agent_input, memory)

        # Inject individual messages as framework chat_history
        agent_context.chat_history = chat_messages
        agent_context.extra['chat_history_cache_boundary'] = num_old

        result = await self.customized_async_execute(
            input_object, agent_input, memory, llm,
            agent_context=agent_context, **kwargs)

        # ---- Phase 5: Post-processing ----
        # Bot message recording is handled by SendQQMessageTool on each send,
        # so we don't need to record here (tool sends may be multiple messages).

        return result

    # ==============================================================
    # Agent context creation
    # ==============================================================

    def _create_agent_context(self, input_object: InputObject,
                              agent_input: dict, memory: Memory) -> AgentContext:
        ctx = super()._create_agent_context(input_object, agent_input, memory)
        ctx.extra['onebot_client'] = input_object.get_data('onebot_client')
        ctx.extra['send_scene'] = input_object.get_data('send_scene') or 'group'
        ctx.extra['send_target_group_id'] = agent_input.get('group_id')
        ctx.extra['send_target_user_id'] = agent_input.get('user_id')
        ctx.extra['trigger_message_id'] = input_object.get_data('trigger_message_id')
        ctx.extra['bot_message_ids'] = input_object.get_data('bot_message_ids')
        ctx.extra['memory'] = memory
        ctx.extra['bot_qq_id'] = agent_input.get('bot_id', BOT_SENDER_ID)
        ctx.extra['bot_name'] = (agent_input.get('bot_names') or ['bot'])[0]
        return ctx

    # ==============================================================
    # LLM invocation — inject cache_control for prompt caching
    # ==============================================================

    async def async_invoke_llm(self, llm: LLM, messages: list,
                               input_object: InputObject,
                               tools_schema: list[dict] = None,
                               agent_context: AgentContext = None,
                               **kwargs) -> LLMOutput:
        """Override to inject cache_control breakpoints for Claude prompt caching.

        4 breakpoints:
          ① tools_schema[-1]           — tool definitions (stable)
          ② system message             — core_persona + 行为准则 (stable per session)
          ③ chat history boundary      — last "old" message (stable between calls)
          ④ last tool-role message     — accumulates across tool-calling rounds
        """
        from agentuniverse.llm.transfer_utils import au_messages_to_openai

        # ① Cache tool definitions: mark the last tool schema
        if tools_schema:
            tools_schema = [*tools_schema]  # shallow copy
            tools_schema[-1] = {
                **tools_schema[-1],
                'cache_control': {'type': 'ephemeral'},
            }

        openai_messages = au_messages_to_openai(messages)

        # Find the last tool-role message index for breakpoint ④
        last_tool_idx = None
        for i in range(len(openai_messages) - 1, -1, -1):
            if openai_messages[i].get('role') == 'tool':
                last_tool_idx = i
                break

        # ③ boundary: system(0) + old chat messages(1..boundary)
        cache_boundary = (
            agent_context.extra.get('chat_history_cache_boundary', 0)
            if agent_context else 0
        )

        for i, msg in enumerate(openai_messages):
            role = msg.get('role')
            content = msg.get('content', '')

            # ② Cache system message (core_persona + 行为准则 + target)
            if role == 'system' and isinstance(content, str) and content:
                msg['content'] = [
                    {'type': 'text', 'text': content,
                     'cache_control': {'type': 'ephemeral'}},
                ]

            # ③ Cache last "old" chat history message
            elif i == cache_boundary and cache_boundary > 0:
                self._inject_cache_control(msg)

            # ④ Cache last tool result (grows each round in tool-calling loop)
            elif role == 'tool' and i == last_tool_idx:
                if isinstance(content, str):
                    msg['content'] = [
                        {'type': 'text', 'text': content,
                         'cache_control': {'type': 'ephemeral'}},
                    ]

        return await super().async_invoke_llm(
            llm, openai_messages, input_object,
            tools_schema=tools_schema, agent_context=agent_context, **kwargs)

    @staticmethod
    def _inject_cache_control(msg: dict) -> None:
        """Add ``cache_control`` to an OpenAI message dict (text or multimodal)."""
        content = msg.get('content', '')
        if isinstance(content, str) and content:
            msg['content'] = [
                {'type': 'text', 'text': content,
                 'cache_control': {'type': 'ephemeral'}},
            ]
        elif isinstance(content, list) and content:
            last = content[-1]
            content[-1] = {**last, 'cache_control': {'type': 'ephemeral'}}

    # ==============================================================
    # Chat history message builder
    # ==============================================================

    @staticmethod
    def _build_chat_messages(
        recent_messages: list[dict],
        bot_id: str,
        id_mapping=None,
        url_to_path: dict[str, str] | None = None,
        max_images: int = _MAX_IMAGES,
    ) -> list[Message]:
        """Convert WorkingMemory dicts into individual ``Message`` objects.

        - Bot messages → ``ChatMessageEnum.AI`` (assistant role)
        - Others → ``ChatMessageEnum.HUMAN`` (user role) with ``[time] name:``
        - Images are embedded inline for the most recent *max_images* images.
        """
        if not recent_messages:
            return []

        url_to_path = url_to_path or {}
        now = time.time()

        # Determine which image URLs will be embedded (most recent N)
        embeddable_candidates: list[str] = []
        for m in recent_messages:
            for url in (m.get('image_urls') or []):
                if url in url_to_path:
                    embeddable_candidates.append(url)
        embeddable_urls = set(embeddable_candidates[-max_images:])

        result: list[Message] = []
        for idx, m in enumerate(recent_messages):
            sender_id = m.get('sender_id', '')
            stored_name = m.get('sender_name', '???')
            ts = format_message_time(m.get('timestamp', 0), now)
            content_text = m.get('content', '')
            msg_image_urls = m.get('image_urls') or []
            is_bot = (sender_id == bot_id)

            if is_bot:
                # Represent bot's own message as tool_call + tool_result
                # so the LLM learns to use the send_qq_message tool.
                clean = content_text.replace('[图片]', '').strip()
                text = clean or content_text
                call_id = f'hist_{idx}'
                result.append(Message(
                    type=ChatMessageEnum.ASSISTANT,
                    tool_calls=[
                        ToolCall.create(
                            id=call_id,
                            name='send_qq_message',
                            arguments={'text': text},
                        )
                    ],
                ))
                result.append(Message(
                    type=ChatMessageEnum.TOOL,
                    content='消息发送成功',
                    tool_call_id=call_id,
                    name='send_qq_message',
                ))
            else:
                # Resolve display name
                if id_mapping and sender_id:
                    name = id_mapping.get_user_name(sender_id) or stored_name
                else:
                    name = stored_name
                header = f'[{ts}] {name}: '

                has_embeddable = any(
                    u in embeddable_urls for u in msg_image_urls)

                if msg_image_urls and has_embeddable:
                    content = _build_multimodal_content(
                        header, content_text, msg_image_urls,
                        url_to_path, embeddable_urls,
                    )
                    result.append(Message(
                        type=ChatMessageEnum.HUMAN,
                        content=content,
                    ))
                else:
                    result.append(Message(
                        type=ChatMessageEnum.HUMAN,
                        content=header + content_text,
                    ))

        return result

    # ==============================================================
    # Image download helper
    # ==============================================================

    @staticmethod
    def _lookup_history_images(
        recent_messages: list[dict],
    ) -> dict[str, str]:
        """Look up already-cached images for recent messages (no network I/O).

        Phase E downloads images when messages first arrive (URLs are fresh).
        Here we only check the local cache — expired CDN URLs are never hit.
        """
        from qq_social_bot_app.intelligence.social_memory.image_cache import (
            lookup_cached_images,
        )
        scope = recent_messages[-_IMAGE_DOWNLOAD_SCOPE:]
        urls: list[str] = []
        for m in scope:
            urls.extend(m.get('image_urls') or [])
        if not urls:
            return {}
        return lookup_cached_images(urls)

    # ==============================================================
    # Async private helpers
    # ==============================================================

    async def _async_ingest_messages(self, memory: Memory,
                                     messages: list[GroupMessage]) -> None:
        if not messages:
            return
        if hasattr(memory, 'async_add_group_messages'):
            await memory.async_add_group_messages(messages)
        elif hasattr(memory, 'async_add_group_message'):
            for msg in messages:
                await memory.async_add_group_message(msg)

    @staticmethod
    def _format_trigger_messages(triggers: list[GroupMessage]) -> str:
        now = time.time()
        lines = []
        for msg in triggers:
            ts = format_message_time(msg.timestamp, now)
            lines.append(f'[{ts}] {msg.sender_name}: {msg.content}')
        return '\n'.join(lines)
