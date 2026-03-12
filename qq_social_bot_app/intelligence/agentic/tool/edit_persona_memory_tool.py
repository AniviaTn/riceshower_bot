"""edit_persona_memory tool – lets the bot edit its own long-term memory."""
import asyncio
import logging

from agentuniverse.agent.action.tool.tool import Tool

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_markdown_service,
)

logger = logging.getLogger(__name__)

_LTM_PATH = 'persona/long_term_memory.md'


class EditPersonaMemoryTool(Tool):
    """Read and overwrite persona/long_term_memory.md.

    The tool operates in two modes depending on whether *content* is provided:
    - **read** (content is empty or omitted): returns the current file content.
    - **write** (content is provided): overwrites the file and invalidates the
      persona cache so the next conversation picks up the change.

    The file path is hardcoded to prevent the bot from writing to other
    persona files.
    """

    require_agent_context: bool = True

    async def async_execute(self, content: str = '', *,
                            agent_context=None, **kwargs) -> str:
        try:
            md = get_markdown_service()
        except RuntimeError as e:
            return f'Error: {e}'

        # --- Read mode ---
        if not content:
            def _read():
                return md.read_file(_LTM_PATH)

            try:
                current = await asyncio.to_thread(_read)
            except Exception as e:
                logger.exception('edit_persona_memory read failed')
                return f'Error reading long-term memory: {e}'

            if current is None:
                return '（长期记忆文件尚不存在，你可以直接写入内容来创建它。）'
            return current

        # --- Write mode ---
        def _write():
            md.write_file(_LTM_PATH, content)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            logger.exception('edit_persona_memory write failed')
            return f'Error writing long-term memory: {e}'

        # Invalidate persona cache so next build_context reloads
        if agent_context:
            memory = agent_context.extra.get('memory')
            if memory and hasattr(memory, 'invalidate_persona_cache'):
                memory.invalidate_persona_cache()

        return '长期记忆已更新。'

    def execute(self, **kwargs) -> str:
        raise NotImplementedError('EditPersonaMemoryTool is async-only.')
