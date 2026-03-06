"""memory_read tool – lets the bot read a specific memory file."""
import asyncio
import logging

from agentuniverse.agent.action.tool.tool import Tool

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_markdown_service,
)

logger = logging.getLogger(__name__)


class MemoryReadTool(Tool):
    """Read a Markdown memory file by path."""

    async def async_execute(self, path: str, **kwargs) -> str:
        try:
            md = get_markdown_service()
        except RuntimeError as e:
            return f'Error: {e}'

        try:
            md._resolve(path)
        except ValueError as e:
            return f'Error: {e}'

        def _read():
            return md.read_file(path)

        try:
            content = await asyncio.to_thread(_read)
        except Exception as e:
            logger.exception('memory_read failed for %s', path)
            return f'Error reading file: {e}'

        if content is None:
            return f'文件不存在: {path}'

        return content

    def execute(self, **kwargs) -> str:
        raise NotImplementedError('MemoryReadTool is async-only.')
