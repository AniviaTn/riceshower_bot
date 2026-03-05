"""QQ group chat social Agent.

External API is only agent.run() / agent.async_run():

  # Single message (real-time push)
  output = await agent.async_run(messages=[msg], bot_id='bot_self', bot_names=['小U'], must_reply=True)

  # Batch messages (periodic / buffered)
  output = await agent.async_run(messages=[msg1, msg2, ...], bot_id='bot_self', bot_names=['小U'], must_reply=False)

  response = output.get_data('output', '')
  # response is '' when the bot decides not to reply

Internally, async_execute() handles the full pipeline:
  1. Ingest all messages into WorkingMemory
  2. If must_reply: build context → run LLM → post-process
  3. If not must_reply: build context → probability LLM call → if pass, run LLM → post-process
"""
import asyncio
import json
import logging
import random
import time

from agentuniverse.agent.input_object import InputObject
from agentuniverse.agent.memory.memory import Memory
from agentuniverse.agent.template.agent_template import AgentTemplate
from agentuniverse.llm.llm import LLM
from agentuniverse.llm.llm_manager import LLMManager
from agentuniverse.ai_context.agent_context import AgentContext

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.working_memory import (
    format_message_time,
)

logger = logging.getLogger(__name__)

# Identifier used when the bot writes its own replies into WorkingMemory
BOT_SENDER_ID = 'bot_self'

# Probability thresholds for passive reply decision
PROB_FLOOR = 0.2   # below this → always skip
PROB_CEIL = 0.7    # above this → always reply
# between floor and ceil → random roll

PROBABILITY_JUDGE_PROMPT = """\
现在你需要判断是否要主动加入当前对话。

下面的聊天记录中，标记为 "Bot" 的发言是你之前的回复。
请重点审视**你上一次发言之后**到现在的所有消息，判断其中是否有你想要回应的内容。
如果你从未发言过，则审视全部消息。

判断标准：
1. **话题相关性**：对话涉及你擅长的领域、或大家在讨论你相关的话题 → 概率偏高
2. **有人需要帮助**：有人在提问或寻求建议，你可能能帮上忙 → 概率偏高
3. **氛围邀请**：群聊氛围活跃且友好，适合你自然插话 → 概率略高
4. **お兄様参与**：如果お兄様（肥猪/小花猪）在你上次回复之后有发言，你会更想回复 → 概率明显偏高
5. **消息太少**：你上次回复之后只有 1-2 条简短消息，通常不值得主动加入 → 概率偏低
6. **深夜时段**：当前如果是深夜（00:00-07:00），除非有明确需要，否则降低参与意愿 → 概率偏低
7. **无关闲聊**：纯粹的成员间私人闲聊、水群刷屏、表情包大战 → 概率偏低
8. **已有充分讨论**：话题已经被讨论完毕，没有新的切入点 → 概率偏低

输出一个 0 到 1 之间的概率值，表示你想要回复的意愿。
直接输出 JSON，不要输出任何其他文字、解释或 markdown 标记：
{"probability": 0.7, "reason": "简短原因"}"""


class QQSocialAgent(AgentTemplate):
    """QQ group chat social AI assistant.

    Only entry point: agent.async_run(messages=[...], bot_id=..., bot_names=[...], must_reply=True/False)
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
        agent_input['must_reply'] = (
            input_object.get_data('must_reply', True))

        return agent_input

    # ==============================================================
    # execute – sync (kept for backward compat, delegates to framework)
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
        bot_id = agent_input.get('bot_id', BOT_SENDER_ID)
        bot_names = agent_input.get('bot_names', ['bot', 'Bot'])
        must_reply = agent_input.get('must_reply', True)

        # Ensure async init
        if hasattr(memory, '_async_ensure_init'):
            await memory._async_ensure_init()

        # ---- Phase 1: Ingest all messages into WorkingMemory ----
        if messages:
            await self._async_ingest_messages(memory, messages)

        # ---- Phase 2: Determine trigger messages and path ----
        if messages:
            if must_reply:
                # Hard rule triggered: last message is the trigger, rest are context
                primary = messages[-1]
                agent_input['user_id'] = primary.sender_id
                agent_input['user_name'] = primary.sender_name
                agent_input['trigger_user_ids'] = list(
                    dict.fromkeys(m.sender_id for m in messages))
                agent_input['trigger_messages'] = (
                    self._format_trigger_messages(messages))
            else:
                # Periodic check: use last few messages as trigger anchor
                recent = messages[-3:] if len(messages) > 3 else messages
                primary = recent[-1]
                agent_input['user_id'] = primary.sender_id
                agent_input['user_name'] = primary.sender_name
                agent_input['trigger_user_ids'] = list(
                    dict.fromkeys(m.sender_id for m in messages))
                agent_input['trigger_messages'] = (
                    self._format_trigger_messages(recent))
        else:
            # Direct-input mode (no GroupMessage list)
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

        profile = self.agent_model.profile or {}
        if 'core_persona' in profile and 'core_persona' not in agent_input:
            agent_input['core_persona'] = profile['core_persona']

        # ---- Phase 3.5: Probability check (only when must_reply=False) ----
        if not must_reply:
            probability = await self._async_judge_reply_probability(
                agent_input, memory, **kwargs)

            if probability <= PROB_FLOOR:
                should_reply = False
                decision_reason = 'below floor'
            elif probability >= PROB_CEIL:
                should_reply = True
                decision_reason = 'above ceil'
            else:
                roll = random.random()
                should_reply = roll < probability
                decision_reason = f'roll={roll:.2f}'

            logger.info(
                'Probability check for group %s: p=%.2f %s → %s',
                group_id, probability, decision_reason,
                'REPLY' if should_reply else 'SKIP')

            if not should_reply:
                return {'output': ''}

        # ---- Phase 4: Run LLM (async) ----
        llm: LLM = self.process_llm(**kwargs)
        agent_context = self._create_agent_context(
            input_object, agent_input, memory)
        result = await self.customized_async_execute(
            input_object, agent_input, memory, llm,
            agent_context=agent_context, **kwargs)

        # ---- Phase 5: Post-processing ----
        bot_response = result.get('output', '')

        # Write bot reply into WorkingMemory
        if bot_response and hasattr(memory, 'async_add_group_message'):
            bot_msg = GroupMessage(
                content=bot_response,
                sender_id=BOT_SENDER_ID,
                sender_name='Bot',
                group_id=group_id,
            )
            await memory.async_add_group_message(bot_msg)

        # Extract candidate memories from full conversation
        if hasattr(memory, 'async_extract_and_store_candidates_from_context'):
            try:
                await memory.async_extract_and_store_candidates_from_context(
                    group_id=group_id, bot_response=bot_response)
            except Exception:
                logger.debug('Candidate extraction failed', exc_info=True)

        # Consolidation (promote eligible candidates)
        if hasattr(memory, 'async_run_consolidation'):
            try:
                await memory.async_run_consolidation()
            except Exception:
                logger.debug('Memory consolidation failed', exc_info=True)

        return result

    # ==============================================================
    # Agent context creation
    # ==============================================================

    def _create_agent_context(self, input_object: InputObject,
                              agent_input: dict, memory: Memory) -> AgentContext:
        """Inject OneBot runtime dependencies into AgentContext.extra."""
        ctx = super()._create_agent_context(input_object, agent_input, memory)
        ctx.extra['onebot_client'] = input_object.get_data('onebot_client')
        ctx.extra['send_scene'] = input_object.get_data('send_scene') or 'group'
        ctx.extra['send_target_group_id'] = agent_input.get('group_id')
        ctx.extra['send_target_user_id'] = agent_input.get('user_id')
        ctx.extra['trigger_message_id'] = input_object.get_data('trigger_message_id')
        ctx.extra['bot_message_ids'] = input_object.get_data('bot_message_ids')
        return ctx

    # ==============================================================
    # Async private helpers
    # ==============================================================

    async def _async_ingest_messages(self, memory: Memory,
                                     messages: list[GroupMessage]) -> None:
        """Write messages into WorkingMemory and update interaction counters."""
        if not messages:
            return

        # Batch write (single Redis pipeline) + topic detection
        if hasattr(memory, 'async_add_group_messages'):
            await memory.async_add_group_messages(messages)
        elif hasattr(memory, 'async_add_group_message'):
            for msg in messages:
                await memory.async_add_group_message(msg)

        # Update interaction counters (SQLite → to_thread)
        if hasattr(memory, '_service'):
            try:
                memory._ensure_init()
                def _update_counters():
                    for msg in messages:
                        if msg.sender_id and msg.group_id:
                            memory._service.increment_interaction(
                                msg.sender_id, msg.group_id)
                await asyncio.to_thread(_update_counters)
            except Exception:
                logger.debug('Failed to update interaction counters',
                             exc_info=True)

    # ----------------------------------------------------------
    # Probability judgment (replaces old passive check + decision agent)
    # ----------------------------------------------------------

    async def _async_judge_reply_probability(self, agent_input: dict,
                                             memory: Memory,
                                             **kwargs) -> float:
        """Use the main LLM with full persona to judge reply probability.

        Returns a float between 0.0 and 1.0.
        """
        profile = self.agent_model.profile or {}
        core_persona = agent_input.get('core_persona', '') or profile.get('core_persona', '')

        # Build system prompt: full persona + probability judgment instructions
        system_prompt = core_persona.rstrip() + '\n\n' + PROBABILITY_JUDGE_PROMPT

        # Build user content from the context already assembled in Phase 3
        # chat_history already contains ALL messages (including Bot's own replies)
        # so the LLM can locate "since my last reply" on its own.
        chat_history = agent_input.get('chat_history', '')
        social_context = agent_input.get('social_context', '')
        current_topic = agent_input.get('current_topic', '')
        current_time = agent_input.get('current_time', '')

        user_parts = []
        if current_time:
            user_parts.append(f'当前时间：{current_time}')
        if current_topic:
            user_parts.append(f'当前话题：{current_topic}')
        if social_context:
            user_parts.append(f'社交记忆：\n{social_context}')
        if chat_history:
            user_parts.append(f'聊天记录：\n{chat_history}')

        user_content = '\n\n'.join(user_parts)

        llm_messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ]

        # Use the main LLM (same as social_main_llm)
        llm_name = profile.get('llm_model', {}).get('name', '')
        llm = LLMManager().get_instance_obj(llm_name) if llm_name else self.process_llm(**kwargs)

        try:
            output = await llm.acall(messages=llm_messages, streaming=False)
            raw_text = output.text if hasattr(output, 'text') else str(output)
            probability = self._parse_probability(raw_text)
        except Exception:
            logger.exception('Probability judgment LLM call failed')
            probability = 0.0  # Default to not replying on error

        return probability

    @staticmethod
    def _parse_probability(raw_text: str) -> float:
        """Parse the LLM JSON response into a probability float."""
        text = raw_text.strip()

        # Strip markdown code block if present
        if text.startswith('```'):
            lines = text.split('\n')
            lines = [l for l in lines if not l.strip().startswith('```')]
            text = '\n'.join(lines).strip()

        # Extract JSON object
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning('Failed to parse probability output: %s',
                           raw_text[:200])
            return 0.0

        prob = parsed.get('probability', 0.0)
        reason = parsed.get('reason', '')
        logger.info('Probability judgment: p=%s reason=%s', prob, reason)

        try:
            prob = float(prob)
        except (TypeError, ValueError):
            return 0.0

        return max(0.0, min(1.0, prob))

    # ----------------------------------------------------------
    # Trigger message formatting
    # ----------------------------------------------------------

    @staticmethod
    def _format_trigger_messages(triggers: list[GroupMessage]) -> str:
        """Format trigger messages for prompt injection."""
        now = time.time()
        lines = []
        for msg in triggers:
            ts = format_message_time(msg.timestamp, now)
            lines.append(f'[{ts}] {msg.sender_name}: {msg.content}')
        return '\n'.join(lines)
