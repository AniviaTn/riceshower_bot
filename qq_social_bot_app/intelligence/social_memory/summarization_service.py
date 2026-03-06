"""Agent-driven periodic summarization service.

Runs a mini-agent loop with the bot's own persona, giving it memory tools
to review recent chat history and write down what it considers worth remembering.
The agent decides what, where, and how to record — no cold third-party extraction.
"""
import asyncio
import json
import logging
import os
import time

import openai

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_id_mapping_service,
    get_markdown_service,
    get_embedding_service,
    get_checkpoint_store,
    get_data_root,
)
from qq_social_bot_app.intelligence.social_memory.raw_message_store import RawMessageStore
from qq_social_bot_app.intelligence.social_memory.markdown_memory import MarkdownMemoryService

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK = 86400.0   # 24h default for first-run
_MIN_MESSAGES = 20
_MAX_TOOL_ROUNDS = 100         # safety cap on agentic loop iterations
_MODEL = 'anthropic/claude-sonnet-4.5'

# ──────────────────────────────────────────────
# OpenAI function-calling tool definitions
# ──────────────────────────────────────────────
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": (
                "写入一条记忆到文件系统。\n\n"
                "目录结构：\n"
                "- people/{qq_id}/{文件名}.md — 关于某个人的记忆\n"
                "- groups/{group_id}/{文件名}.md — 关于某个群的事件/讨论\n"
                "- notes/{文件名}.md — 通用笔记\n"
                "- people/{qq_id}/profile.md — 某人的长期画像（每次对话自动加载）\n"
                "- groups/{group_id}/profile.md — 群的长期描述（每次对话自动加载）\n\n"
                "文件名格式：{日期}_{简短描述}.md，例如 2026-03-06_小明聊了养猫的事.md\n\n"
                "profile.md 适合存：昵称、性格、兴趣爱好、重要事实。"
                "不要在 profile 里存时间敏感内容。\n\n"
                "写入时会自动检查重复，如果有高度相似的已有记忆会收到警告。"
                "确认要写可在 content 开头加 FORCE: 前缀。\n\n"
                "不可写入：persona/ 目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对路径，如 people/12345/2026-03-06_养猫.md",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown 格式的记忆内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": (
                "读取一个已有的记忆文件。可以用来查看某人的 profile 或已有记忆，"
                "避免重复写入或覆盖重要内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对路径，如 people/12345/profile.md",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "语义搜索已有记忆。在写入前可以先搜一下，看看是否已经记录过类似内容。\n"
                "scope 可选 people/groups/notes/all（默认 all）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或描述",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["people", "groups", "notes", "all"],
                        "description": "搜索范围，默认 all",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "限定某个用户（可选）",
                    },
                    "group_id": {
                        "type": "string",
                        "description": "限定某个群（可选）",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

_REVIEW_INSTRUCTION = """\
现在是你的定期记忆回顾时间。下面是你最近还没整理过的群聊记录。

请回顾这些对话，把你觉得值得记住的东西用 memory_write 写下来。你可以：
- 记录重要事件、约定、承诺
- 记录群友透露的新信息（兴趣、近况、情感变化）——写到对应的 people/{id}/ 下
- 记录有趣或重要的群讨论——写到 groups/{group_id}/ 下
- 如果某人有值得更新的画像信息（新发现的兴趣、性格特点等），先用 memory_read 读一下他现有的 profile.md，再决定是否更新
- 如果没什么值得记录的，不用勉强写

写的时候用你自己的语气，就像在写日记。不要写成冷冰冰的会议纪要。
每个独立话题/事件写一个单独的文件，不要把所有东西塞进一个文件。

全部回顾完后，说"回顾完毕"。"""


class SummarizationService:
    """Agent-driven periodic summarization using tool-calling loop."""

    def __init__(self, raw_store: RawMessageStore | None = None):
        self._raw_store = raw_store or RawMessageStore(
            base_dir=os.path.join(get_data_root(), 'raw_messages'))
        self._client = openai.AsyncOpenAI(
            api_key=os.environ.get('ZENMUX_API_KEY', ''),
            base_url='https://zenmux.ai/api/v1',
        )

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    async def summarize_group(self, group_id: str,
                              force: bool = False) -> list[str]:
        """Run the agent to review & record memories for a group.

        Returns list of file paths the agent wrote (may be empty).
        """
        checkpoint_store = get_checkpoint_store()
        id_mapping = get_id_mapping_service()

        # 1. Determine time range
        cp = checkpoint_store.get_checkpoint(group_id)
        since_ts = cp['last_checkpoint'] if cp else time.time() - _DEFAULT_LOOKBACK
        until_ts = time.time()

        # 2. Read raw messages
        messages = await asyncio.to_thread(
            self._raw_store.read_messages_since, group_id, since_ts, until_ts)

        if not messages:
            logger.debug('No messages for group %s since %.0f', group_id, since_ts)
            return []

        if len(messages) < _MIN_MESSAGES and not force:
            logger.debug('Only %d messages for group %s (need %d), skipping',
                         len(messages), group_id, _MIN_MESSAGES)
            return []

        # 3. Format chat history with current display names
        lines = []
        for msg in messages:
            sender_id = msg.get('sender_id', '')
            name = id_mapping.get_user_name(sender_id) or msg.get('sender_name', '???')
            content = msg.get('content', '')
            ts = msg.get('timestamp', 0)
            ts_str = time.strftime('%H:%M', time.localtime(ts)) if ts else '??:??'
            lines.append(f'[{ts_str}] {name}(ID:{sender_id}): {content}')
        chat_text = '\n'.join(lines)

        # 4. Load persona
        persona = await self._load_persona()
        group_name = id_mapping.get_group_name(group_id) or group_id

        # 5. Build initial messages
        system_prompt = f'{persona}\n\n{_REVIEW_INSTRUCTION}'
        user_msg = (
            f'群「{group_name}」(ID: {group_id}) 最近的聊天记录：\n\n{chat_text}'
        )

        api_messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_msg},
        ]

        # 6. Agentic tool-calling loop
        logger.info('[Summarize:%s] Starting agent loop (%d messages, %d chars)',
                     group_id, len(messages), len(chat_text))
        files_written: list[str] = []

        for round_idx in range(_MAX_TOOL_ROUNDS):
            logger.info('[Summarize:%s] Round %d — calling LLM...', group_id, round_idx + 1)
            try:
                response = await self._client.chat.completions.create(
                    model=_MODEL,
                    messages=api_messages,
                    tools=_TOOLS,
                    temperature=0.7,
                    max_tokens=4096,
                )
            except Exception:
                logger.exception('[Summarize:%s] LLM call failed (round %d)',
                                  group_id, round_idx + 1)
                break

            choice = response.choices[0]
            assistant_msg = choice.message

            # Serialize assistant message back into conversation
            api_messages.append(self._serialize_message(assistant_msg))

            if not assistant_msg.tool_calls:
                # Agent is done (said "回顾完毕" or similar)
                final_text = (assistant_msg.content or '')[:100]
                logger.info('[Summarize:%s] Agent finished: "%s"', group_id, final_text)
                break

            # Execute each tool call
            tool_names = [tc.function.name for tc in assistant_msg.tool_calls]
            logger.info('[Summarize:%s] Round %d — %d tool call(s): %s',
                         group_id, round_idx + 1, len(tool_names), tool_names)

            for tc in assistant_msg.tool_calls:
                result = await self._execute_tool(tc)
                result_preview = result[:120].replace('\n', ' ')
                logger.info('[Summarize:%s]   %s(%s) -> %s',
                             group_id, tc.function.name,
                             tc.function.arguments[:80], result_preview)
                api_messages.append({
                    'role': 'tool',
                    'tool_call_id': tc.id,
                    'content': result,
                })
                # Track written files
                if tc.function.name == 'memory_write' and 'Successfully' in result:
                    try:
                        args = json.loads(tc.function.arguments)
                        files_written.append(args.get('path', ''))
                    except (json.JSONDecodeError, KeyError):
                        pass
        else:
            logger.warning('[Summarize:%s] Hit max rounds (%d), stopping',
                            group_id, _MAX_TOOL_ROUNDS)

        # 7. Update checkpoint regardless of how many files were written
        last_msg_ts = messages[-1].get('timestamp', until_ts)
        checkpoint_store.set_checkpoint(
            group_id, last_msg_ts,
            files_written[0] if files_written else None,
        )

        logger.info('Summarization for group %s complete: %d files written %s',
                     group_id, len(files_written), files_written)
        return files_written

    # ──────────────────────────────────────────
    # Tool execution
    # ──────────────────────────────────────────

    async def _execute_tool(self, tool_call) -> str:
        """Dispatch a tool call to the appropriate handler."""
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            return 'Error: invalid JSON in tool arguments'

        if name == 'memory_write':
            return await self._tool_memory_write(
                args.get('path', ''), args.get('content', ''))
        elif name == 'memory_read':
            return await self._tool_memory_read(args.get('path', ''))
        elif name == 'memory_search':
            return await self._tool_memory_search(
                args.get('query', ''),
                scope=args.get('scope'),
                user_id=args.get('user_id'),
                group_id=args.get('group_id'),
            )
        else:
            return f'Error: unknown tool "{name}"'

    async def _tool_memory_write(self, path: str, content: str) -> str:
        """Write memory file with dedup check. Same logic as MemoryWriteTool."""
        md = get_markdown_service()
        embed = get_embedding_service()

        try:
            md._resolve(path)
            md._assert_writable(path)
        except (ValueError, PermissionError) as e:
            return f'Error: {e}'

        # Handle FORCE: prefix
        force_write = content.startswith('FORCE:')
        if force_write:
            content = content[len('FORCE:'):].lstrip()

        # Dedup check
        if not force_write and MarkdownMemoryService.should_index(path):
            warning = await asyncio.to_thread(
                self._check_dedup, embed, path, content)
            if warning:
                return warning

        def _write():
            md.write_file(path, content)
            if MarkdownMemoryService.should_index(path):
                embed.index_file(path, content)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            logger.exception('memory_write failed for %s', path)
            return f'Error writing file: {e}'

        return f'Successfully wrote {path}'

    async def _tool_memory_read(self, path: str) -> str:
        """Read a memory file."""
        md = get_markdown_service()
        try:
            md._resolve(path)
        except ValueError as e:
            return f'Error: {e}'

        content = await asyncio.to_thread(md.read_file, path)
        if content is None:
            return f'文件不存在: {path}'
        return content

    async def _tool_memory_search(self, query: str, scope: str | None = None,
                                   user_id: str | None = None,
                                   group_id: str | None = None) -> str:
        """Semantic search over memory files."""
        embed = get_embedding_service()

        def _search():
            return embed.search(
                query=query, n_results=5,
                scope=scope if scope and scope != 'all' else None,
                user_id=user_id, group_id=group_id,
            )

        try:
            results = await asyncio.to_thread(_search)
        except Exception as e:
            return f'Error searching: {e}'

        if not results:
            return '没有找到相关记忆。'

        lines = []
        for r in results:
            score = r.get('score', 0)
            rpath = r.get('path', '')
            rcontent = r.get('content', '')
            if len(rcontent) > 500:
                rcontent = rcontent[:500] + '...'
            lines.append(f'--- {rpath} (相关度: {score:.2f}) ---\n{rcontent}')
        return '\n\n'.join(lines)

    @staticmethod
    def _check_dedup(embed, path: str, content: str) -> str | None:
        """Check for semantically similar existing files under the same entity."""
        parts = path.split('/')
        scope = parts[0] if parts else None
        entity_id = parts[1] if len(parts) > 2 else None

        try:
            results = embed.search(
                query=content, n_results=3,
                scope=scope,
                user_id=entity_id if scope == 'people' else None,
                group_id=entity_id if scope == 'groups' else None,
            )
        except Exception:
            return None

        for item in results:
            if item['path'] == path:
                continue
            score = item.get('score', 0)
            if score >= 0.90:
                preview = (item.get('content', '') or '')[:200]
                logger.info('[Dedup] BLOCKED %s — similar to %s (score=%.3f, threshold=0.90)',
                            path, item['path'], score)
                return (
                    f'Warning: 发现相似记忆 (相似度: {score:.2f})\n'
                    f'已有文件: {item["path"]}\n'
                    f'内容预览: {preview}...\n\n'
                    f'如确定要写入，在 content 开头加 FORCE: 前缀。'
                )
            elif score >= 0.80:
                logger.info('[Dedup] PASSED %s — closest match: %s (score=%.3f, threshold=0.90)',
                            path, item['path'], score)
        return None

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    async def _load_persona(self) -> str:
        """Load the agent's persona from Markdown files."""
        md = get_markdown_service()

        def _read():
            parts = []
            core = md.read_file('persona/core_persona.md')
            if core:
                parts.append(core.strip())
            ltm = md.read_file('persona/long_term_memory.md')
            if ltm:
                parts.append(ltm.strip())
            return '\n\n'.join(parts)

        return await asyncio.to_thread(_read)

    @staticmethod
    def _serialize_message(msg) -> dict:
        """Serialize an OpenAI ChatCompletionMessage to a plain dict."""
        d: dict = {'role': 'assistant', 'content': msg.content or ''}
        if msg.tool_calls:
            d['tool_calls'] = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.function.name,
                        'arguments': tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return d
