"""memory_search tool – semantic search over memory files via ChromaDB."""
import asyncio
import logging
from typing import Optional

from agentuniverse.agent.action.tool.tool import Tool

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_embedding_service,
)

logger = logging.getLogger(__name__)


class MemorySearchTool(Tool):
    """Semantic search over indexed memory files."""

    async def async_execute(self, query: str,
                            scope: Optional[str] = None,
                            user_id: Optional[str] = None,
                            group_id: Optional[str] = None,
                            **kwargs) -> str:
        try:
            embed = get_embedding_service()
        except RuntimeError as e:
            return f'Error: {e}'

        def _search():
            return embed.search(
                query=query,
                n_results=5,
                scope=scope,
                user_id=user_id,
                group_id=group_id,
            )

        try:
            results = await asyncio.to_thread(_search)
        except Exception as e:
            logger.exception('memory_search failed')
            return f'Error searching: {e}'

        if not results:
            return '没有找到相关记忆。'

        lines = []
        for r in results:
            score = r.get('score', 0)
            path = r.get('path', '')
            content = r.get('content', '')
            # Truncate long content
            if len(content) > 500:
                content = content[:500] + '...'
            lines.append(f'--- {path} (相关度: {score:.2f}) ---\n{content}')

        return '\n\n'.join(lines)

    def execute(self, **kwargs) -> str:
        raise NotImplementedError('MemorySearchTool is async-only.')
