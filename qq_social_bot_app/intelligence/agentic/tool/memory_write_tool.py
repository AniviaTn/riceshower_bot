"""memory_write tool – lets the bot write Markdown memory files."""
import asyncio
import logging

from agentuniverse.agent.action.tool.tool import Tool

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_markdown_service, get_embedding_service,
)
from qq_social_bot_app.intelligence.social_memory.markdown_memory import MarkdownMemoryService

logger = logging.getLogger(__name__)

_DEDUP_THRESHOLD = 0.90
_FORCE_PREFIX = 'FORCE:'


class MemoryWriteTool(Tool):
    """Write a Markdown memory file and auto-update the embedding index."""

    async def async_execute(self, path: str, content: str, **kwargs) -> str:
        try:
            md = get_markdown_service()
            embed = get_embedding_service()
        except RuntimeError as e:
            return f'Error: {e}'

        # Validate
        try:
            md._resolve(path)
            md._assert_writable(path)
        except (ValueError, PermissionError) as e:
            return f'Error: {e}'

        # Handle FORCE: prefix to bypass dedup
        force_write = content.startswith(_FORCE_PREFIX)
        if force_write:
            content = content[len(_FORCE_PREFIX):].lstrip()

        # Dedup check for indexable files
        if not force_write and MarkdownMemoryService.should_index(path):
            dedup_warning = await asyncio.to_thread(
                self._check_dedup, embed, path, content)
            if dedup_warning:
                return dedup_warning

        # Write file
        def _write():
            md.write_file(path, content)
            # Auto-index if not profile.md
            if MarkdownMemoryService.should_index(path):
                embed.index_file(path, content)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            logger.exception('memory_write failed for %s', path)
            return f'Error writing file: {e}'

        return f'Successfully wrote {path}'

    @staticmethod
    def _check_dedup(embed, path: str, content: str) -> str | None:
        """Check for semantically similar existing files under the same entity.

        Returns a warning string if a duplicate is found, None otherwise.
        """
        # Extract entity scope for targeted search
        parts = path.split('/')
        scope = parts[0] if parts else None
        entity_id = parts[1] if len(parts) > 2 else None

        try:
            results = embed.search(
                query=content,
                n_results=3,
                scope=scope,
                user_id=entity_id if scope == 'people' else None,
                group_id=entity_id if scope == 'groups' else None,
            )
        except Exception:
            logger.debug('Dedup search failed, allowing write', exc_info=True)
            return None

        for item in results:
            # Skip if it's the same file path (overwrite is fine)
            if item['path'] == path:
                continue
            score = item.get('score', 0)
            if score >= _DEDUP_THRESHOLD:
                preview = (item.get('content', '') or '')[:200]
                logger.info('[Dedup] BLOCKED %s — similar to %s (score=%.3f, threshold=%.2f)',
                            path, item['path'], score, _DEDUP_THRESHOLD)
                return (
                    f'Warning: 发现相似记忆文件 (相似度: {score:.2f})\n'
                    f'已有文件: {item["path"]}\n'
                    f'内容预览: {preview}...\n\n'
                    f'如果确定要写入，请在 content 开头加上 FORCE: 前缀强制写入。'
                )
            elif score >= 0.80:
                logger.info('[Dedup] PASSED %s — closest match: %s (score=%.3f, threshold=%.2f)',
                            path, item['path'], score, _DEDUP_THRESHOLD)

        return None

    def execute(self, **kwargs) -> str:
        raise NotImplementedError('MemoryWriteTool is async-only.')
