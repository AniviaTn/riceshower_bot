"""LLM-driven candidate memory extraction from conversation.

Uses a lightweight LLM (e.g. Haiku) to extract structured candidate memories
from recent messages, then stores them in the CandidateMemory buffer.
"""
import asyncio
import json
import logging
import time
from typing import List, Optional

from agentuniverse.llm.llm_manager import LLMManager

from qq_social_bot_app.intelligence.social_memory.social_memory_service import (
    SocialMemoryService,
)

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
你是一个社交记忆分析器。给定一段群聊对话，提取值得记住的候选记忆。

直接输出一个 JSON 数组，不要输出任何其他文字、解释或 markdown 标记。

每个元素的字段：
- "candidate_type": "user_profile" | "group_profile" | "episode" | "relationship" | "persona"
- "user_id": 对应用户ID（群级别信息填空字符串）
- "content": 提取到的信息（dict）
- "sensitivity_level": 0=公开事实 1=日常 2=个人 3=敏感隐私
- "confidence": 0.0~1.0

content 格式按 candidate_type 区分：
- user_profile: preferred_name, alias_list(list, 对话中其他人用来称呼该用户的昵称/外号/简称), style_tags, interest_tags, boundary_tags, stable_facts(dict)
- group_profile: style_tags, tone_tags, common_topics, common_memes
- episode: title, summary, participants(list), mood, tags(list)
- relationship: familiarity, trust, banter_level 相关观察
- persona: core_traits(list), speaking_style(str), learned_boundaries(list), preferred_patterns(list)

规则：
1. 只提取对话中明确支持的信息，不要编造或过度推断
2. 没有值得记录的信息时返回空数组 []
3. persona 类型仅在对话明确体现了对 bot 行为的偏好时才提取
4. alias_list 应包含对话中其他人用来称呼该用户的昵称、简称、外号，不包括用户本身的 sender_name"""


class MemoryExtractor:
    """LLM-driven candidate memory extraction."""

    def __init__(self, llm_name: str = 'social_extractor_llm'):
        self._llm_name = llm_name

    def _get_llm(self):
        return LLMManager().get_instance_obj(self._llm_name)

    def extract_candidates(self, messages: List[dict], group_id: str,
                           existing_profiles: dict = None) -> List[dict]:
        """Call LLM to extract candidate memories from conversation messages.

        Args:
            messages: List of message dicts with 'role', 'content', optionally
                      'user_id' and 'user_name'.
            group_id: The group ID context.
            existing_profiles: Optional dict of existing user/group profiles
                               for context.

        Returns:
            List of candidate dicts ready for SocialMemoryService.add_candidate().
        """
        llm = self._get_llm()
        if not llm:
            logger.warning('Extractor LLM %s not found', self._llm_name)
            return []

        # Build the user message
        conversation_text = self._format_messages(messages)
        context_parts = [f'群ID: {group_id}']
        if existing_profiles:
            context_parts.append(
                f'已有用户画像（仅供参考）:\n{json.dumps(existing_profiles, ensure_ascii=False, indent=1)}')
        context_parts.append(f'对话记录:\n{conversation_text}')
        user_content = '\n\n'.join(context_parts)

        llm_messages = [
            {'role': 'system', 'content': EXTRACTION_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ]

        try:
            output = llm.call(messages=llm_messages, streaming=False)
            raw_text = output.text if hasattr(output, 'text') else str(output)
            candidates = self._parse_candidates(raw_text, group_id)
            return candidates
        except Exception:
            logger.exception('Memory extraction LLM call failed')
            return []

    async def async_extract_candidates(self, messages: List[dict], group_id: str,
                                       existing_profiles: dict = None) -> List[dict]:
        """Async version: call LLM via acall() to extract candidate memories."""
        llm = self._get_llm()
        if not llm:
            logger.warning('Extractor LLM %s not found', self._llm_name)
            return []

        conversation_text = self._format_messages(messages)
        context_parts = [f'群ID: {group_id}']
        if existing_profiles:
            context_parts.append(
                f'已有用户画像（仅供参考）:\n{json.dumps(existing_profiles, ensure_ascii=False, indent=1)}')
        context_parts.append(f'对话记录:\n{conversation_text}')
        user_content = '\n\n'.join(context_parts)

        llm_messages = [
            {'role': 'system', 'content': EXTRACTION_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ]

        try:
            output = await llm.acall(messages=llm_messages, streaming=False)
            raw_text = output.text if hasattr(output, 'text') else str(output)
            candidates = self._parse_candidates(raw_text, group_id)
            return candidates
        except Exception:
            logger.exception('Async memory extraction LLM call failed')
            return []

    def consolidate_candidates(self, service: SocialMemoryService):
        """Promote candidates that have reached sufficient confidence.

        Simple rule-based promotion:
        - confidence >= 0.7 → promote immediately
        - sensitivity_level <= 1 → promote at confidence >= 0.5
        - sensitivity_level >= 3 → never auto-promote (requires future manual review)
        """
        candidates = service.get_pending_candidates(min_confidence=0.3)
        promoted_count = 0
        for c in candidates:
            sensitivity = c.get('sensitivity_level', 0)
            confidence = c.get('confidence', 0)

            # Skip very sensitive items
            if sensitivity >= 3:
                continue

            should_promote = (
                (confidence >= 0.7) or
                (sensitivity <= 1 and confidence >= 0.5)
            )
            if not should_promote:
                continue

            ctype = c.get('candidate_type', '')
            content = c.get('content', {})
            user_id = c.get('user_id', '')
            group_id = c.get('group_id', '')

            try:
                if ctype == 'user_profile' and user_id:
                    service.upsert_user_profile(user_id, content)
                elif ctype == 'group_profile' and group_id:
                    service.upsert_group_profile(group_id, content)
                elif ctype == 'relationship' and user_id and group_id:
                    service.upsert_relationship(user_id, group_id, content)
                elif ctype == 'episode' and group_id:
                    content.setdefault('group_id', group_id)
                    # Propagate sensitivity_level from candidate to episode
                    content.setdefault('sensitivity_level', sensitivity)
                    service.create_episode(content)
                elif ctype == 'persona' and group_id:
                    service.upsert_persona(group_id, content)

                service.promote_candidate(c['id'])
                promoted_count += 1
            except Exception:
                logger.exception('Failed to promote candidate %s', c.get('id'))

        if promoted_count:
            logger.info('Promoted %d candidates to long-term memory', promoted_count)

        # Clean up expired/promoted
        service.cleanup_expired_candidates()

    async def async_consolidate_candidates(self, service: SocialMemoryService):
        """Async version: wraps sync SQLite service calls with asyncio.to_thread()."""
        await asyncio.to_thread(self.consolidate_candidates, service)

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    @staticmethod
    def _format_messages(messages: List[dict]) -> str:
        lines = []
        for m in messages:
            role = m.get('role', 'user')
            name = m.get('user_name', role)
            content = m.get('content', '')
            lines.append(f'{name}: {content}')
        return '\n'.join(lines)

    @staticmethod
    def _extract_json(raw_text: str) -> str:
        """从 LLM 输出中提取 JSON 部分，忽略前后的自然语言文本。"""
        import re

        text = raw_text.strip()

        # 1. 去掉 markdown 代码块包裹
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            text = m.group(1).strip()

        # 2. 找第一个 [ 到最后一个 ] 之间的内容（JSON 数组）
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        # 3. 找第一个 { 到最后一个 }（单个 JSON 对象）
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return text

    @staticmethod
    def _parse_candidates(raw_text: str, group_id: str) -> List[dict]:
        """Parse LLM JSON output into candidate dicts."""
        text = MemoryExtractor._extract_json(raw_text)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning('Failed to parse extractor output as JSON: %s',
                           raw_text[:300])
            return []

        if not isinstance(parsed, list):
            parsed = [parsed]

        candidates = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            candidate = {
                'candidate_type': item.get('candidate_type', 'user_profile'),
                'scope': 'group',
                'group_id': group_id,
                'user_id': item.get('user_id', ''),
                'content': item.get('content', {}),
                'sensitivity_level': item.get('sensitivity_level', 0),
                'confidence': item.get('confidence', 0.5),
                'evidence_count': 1,
                'expire_at': time.time() + 7 * 24 * 3600,  # 7 days
            }
            candidates.append(candidate)
        return candidates
