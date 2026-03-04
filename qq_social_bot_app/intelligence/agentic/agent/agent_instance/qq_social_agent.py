"""QQ group chat social Agent.

External API is only agent.run():

  # Single message (real-time push)
  output = agent.run(messages=[msg], bot_id='bot_self', bot_names=['小U'])

  # Batch messages (scheduled poll)
  output = agent.run(messages=[msg1, msg2, ...], bot_id='bot_self', bot_names=['小U'])

  response = output.get_data('output', '')
  # response is '' when the bot decides not to reply

Internally, execute() handles the full pipeline:
  1. Ingest all messages into WorkingMemory
  2. Determine which messages trigger a response (should_respond)
  3. If any triggers exist, build context and call LLM
  4. Post-turn: write bot reply, extract candidate memories, consolidate
"""
import logging
import random
import time

from agentuniverse.agent.input_object import InputObject
from agentuniverse.agent.memory.memory import Memory
from agentuniverse.agent.template.agent_template import AgentTemplate
from agentuniverse.llm.llm import LLM

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.working_memory import (
    format_message_time,
)

logger = logging.getLogger(__name__)

# Identifier used when the bot writes its own replies into WorkingMemory
BOT_SENDER_ID = 'bot_self'


class QQSocialAgent(AgentTemplate):
    """QQ group chat social AI assistant.

    Only entry point: agent.run(messages=[...], bot_id=..., bot_names=[...])
    """

    def input_keys(self) -> list[str]:
        return []

    def output_keys(self) -> list[str]:
        return ['output']

    def parse_result(self, agent_result: dict) -> dict:
        return {**agent_result, 'output': agent_result.get('output', '')}

    def parse_input(self, input_object: InputObject, agent_input: dict) -> dict:
        """Extract messages and config from InputObject."""
        messages = input_object.get_data('messages')
        group_message = input_object.get_data('group_message')

        if messages and isinstance(messages, list) and len(messages) > 0:
            sorted_msgs = sorted(messages, key=lambda m: m.timestamp)
            agent_input['messages'] = sorted_msgs
            agent_input['group_id'] = sorted_msgs[0].group_id
            agent_input['session_id'] = sorted_msgs[0].group_id
            agent_input['input'] = sorted_msgs[-1].content
        elif group_message:
            # Backward compat: single GroupMessage → wrap in list
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

        return agent_input

    # ==============================================================
    # execute – the full pipeline
    # ==============================================================

    def execute(self, input_object: InputObject, agent_input: dict,
                **kwargs) -> dict:
        memory: Memory = self.process_memory(agent_input, **kwargs)

        group_id = agent_input.get('group_id', '')
        messages = agent_input.get('messages', [])
        bot_id = agent_input.get('bot_id', BOT_SENDER_ID)
        bot_names = agent_input.get('bot_names', ['bot', 'Bot'])

        # ---- Phase 1: Ingest all messages into WorkingMemory ----
        if messages:
            self._ingest_messages(memory, messages)

        # ---- Phase 2: Find trigger messages ----
        if messages:
            triggers = self._find_triggers(
                messages, memory, bot_id, bot_names)
            if not triggers:
                # No message needs a reply → return empty
                return {'output': ''}
            # Derive prompt vars from triggers
            primary = triggers[-1]
            agent_input['user_id'] = primary.sender_id
            agent_input['user_name'] = primary.sender_name
            agent_input['trigger_user_ids'] = list(
                dict.fromkeys(t.sender_id for t in triggers))
            agent_input['trigger_messages'] = (
                self._format_trigger_messages(triggers))
        else:
            # Direct-input mode (no GroupMessage list)
            agent_input.setdefault('trigger_messages',
                                   agent_input.get('input', ''))

        # ---- Phase 3: Build social context ----
        context = memory.build_context(
            session_id=agent_input.get('session_id', ''),
            group_id=group_id,
            user_id=agent_input.get('user_id', ''),
            trigger_user_ids=agent_input.get('trigger_user_ids', []),
        )
        agent_input.update(context)

        profile = self.agent_model.profile or {}
        if 'core_persona' in profile and 'core_persona' not in agent_input:
            agent_input['core_persona'] = profile['core_persona']

        # ---- Phase 4: Run LLM ----
        llm: LLM = self.process_llm(**kwargs)
        agent_context = self._create_agent_context(
            input_object, agent_input, memory)
        result = self.customized_execute(
            input_object, agent_input, memory, llm,
            agent_context=agent_context, **kwargs)

        # ---- Phase 5: Post-processing ----
        bot_response = result.get('output', '')

        # Write bot reply into WorkingMemory
        if bot_response and hasattr(memory, 'add_group_message'):
            bot_msg = GroupMessage(
                content=bot_response,
                sender_id=BOT_SENDER_ID,
                sender_name='Bot',
                group_id=group_id,
            )
            memory.add_group_message(bot_msg)

        # Extract candidate memories from full conversation
        if hasattr(memory, 'extract_and_store_candidates_from_context'):
            try:
                memory.extract_and_store_candidates_from_context(
                    group_id=group_id, bot_response=bot_response)
            except Exception:
                logger.debug('Candidate extraction failed', exc_info=True)

        # Consolidation (promote eligible candidates)
        if hasattr(memory, 'run_consolidation'):
            try:
                memory.run_consolidation()
            except Exception:
                logger.debug('Memory consolidation failed', exc_info=True)

        return result

    # ==============================================================
    # Private helpers
    # ==============================================================

    def _ingest_messages(self, memory: Memory,
                         messages: list[GroupMessage]) -> None:
        """Write messages into WorkingMemory and update interaction counters."""
        if not messages:
            return

        # Batch write (single Redis pipeline) + topic detection
        if hasattr(memory, 'add_group_messages'):
            memory.add_group_messages(messages)
        elif hasattr(memory, 'add_group_message'):
            for msg in messages:
                memory.add_group_message(msg)

        # Update interaction counters
        if hasattr(memory, '_service'):
            try:
                memory._ensure_init()
                for msg in messages:
                    if msg.sender_id and msg.group_id:
                        memory._service.increment_interaction(
                            msg.sender_id, msg.group_id)
            except Exception:
                logger.debug('Failed to update interaction counters',
                             exc_info=True)

    def _find_triggers(self, messages: list[GroupMessage],
                       memory: Memory,
                       bot_id: str, bot_names: list[str],
                       ) -> list[GroupMessage]:
        """Return messages that the bot should reply to."""
        return [m for m in messages
                if self._should_respond(m, memory, bot_id, bot_names)]

    def _should_respond(self, msg: GroupMessage, memory: Memory,
                        bot_id: str, bot_names: list[str]) -> bool:
        """Decide whether the bot should reply to *msg*.

        Rules (first match wins):
        1. Bot ID in msg.at_list           → True
        2. @BotName in text                → True
        3. Bot name mentioned in text      → True
        4. Reply to a bot message          → True
        5. Random probability (tolerance)  → maybe True
        6. Otherwise                       → False
        """
        content = msg.content or ''

        # Rule 1
        if bot_id and bot_id in (msg.at_list or []):
            return True

        # Rule 2
        for name in bot_names:
            if f'@{name}' in content:
                return True

        # Rule 3
        content_lower = content.lower()
        for name in bot_names:
            if name.lower() in content_lower:
                return True

        # Rule 4
        if msg.reply_to:
            if self._is_bot_message(msg.reply_to, msg.group_id, memory):
                return True

        # Rule 5
        prob = self._get_random_respond_probability(msg.group_id, memory)
        return True

        return False

    @staticmethod
    def _is_bot_message(message_id: str, group_id: str,
                        memory: Memory) -> bool:
        """Check whether *message_id* was sent by the bot."""
        if not hasattr(memory, '_working_memory'):
            return False
        memory._ensure_init()
        wm = memory._working_memory
        if not wm:
            return False
        for m in wm.get_recent_messages(group_id, limit=50):
            if m.get('message_id') == message_id:
                return m.get('sender_id') == BOT_SENDER_ID
        return False

    @staticmethod
    def _get_random_respond_probability(group_id: str,
                                        memory: Memory) -> float:
        """Random-reply probability based on group tolerance.

        tolerance 1-3  → 0%
        tolerance 4-6  → 2-5%
        tolerance 7-10 → 5-15%

        00:00-07:00 reduces probability by 90%.
        """
        if not hasattr(memory, '_service'):
            return 0.0
        try:
            memory._ensure_init()
            profile = memory._service.get_group_profile(group_id)
        except Exception:
            return 0.0
        if not profile:
            return 0.0

        tolerance = profile.get('bot_tolerance_level', 5)
        if tolerance <= 3:
            return 0.0
        elif tolerance <= 6:
            probability = 0.02 + (tolerance - 4) * 0.01
        else:
            probability = 0.05 + (tolerance - 7) * 0.033

        hour = time.localtime().tm_hour
        if hour < 7:
            probability *= 0.1

        return probability

    @staticmethod
    def _format_trigger_messages(triggers: list[GroupMessage]) -> str:
        """Format trigger messages for prompt injection."""
        now = time.time()
        lines = []
        for msg in triggers:
            ts = format_message_time(msg.timestamp, now)
            lines.append(f'[{ts}] {msg.sender_name}: {msg.content}')
        return '\n'.join(lines)
