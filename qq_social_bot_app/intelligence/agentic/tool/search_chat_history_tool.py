"""Tool for searching raw chat history files via shell commands.

Allows the agent to search through JSONL message archives stored at:
    data/raw_messages/{group_id}/{YYYY-MM-DD}/{HH}.jsonl

The tool accepts a natural-language-style query and translates it into
appropriate grep / find commands scoped to the right group and date range.
"""
import os
import subprocess
import time
from typing import Optional

from agentuniverse.agent.action.tool.tool import Tool

# Anchor to qq_social_bot_app/ root.
# search_chat_history_tool.py is at: qq_social_bot_app/intelligence/agentic/tool/
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_DEFAULT_BASE_DIR = os.path.join(_APP_ROOT, 'data', 'raw_messages')


class SearchChatHistoryTool(Tool):
    """Search raw chat history JSONL files using shell commands.

    Input keys:
        group_id  – the group to search in
        keyword   – text pattern to grep for (supports basic regex)
        date      – (optional) date filter, e.g. "2026-03-04" or "2026-03"
        sender    – (optional) filter by sender name
    """

    base_dir: str = _DEFAULT_BASE_DIR

    def execute(self, group_id: str, keyword: str,
                date: Optional[str] = None,
                sender: Optional[str] = None) -> str:
        if not group_id or not keyword:
            return 'Error: group_id and keyword are required.'

        group_dir = os.path.join(self.base_dir, group_id)
        if not os.path.isdir(group_dir):
            return f'No chat history found for group {group_id}.'

        # Determine search scope
        if date:
            # date can be "2026-03-04" (specific day) or "2026-03" (whole month)
            search_path = os.path.join(group_dir, date)
            if not os.path.exists(search_path):
                # Try as prefix match for month-level queries
                search_path = group_dir
                find_name_filter = f'-path "*/{date}*"'
            else:
                find_name_filter = ''
        else:
            search_path = group_dir
            find_name_filter = ''

        # Build grep pattern – combine keyword + optional sender filter
        grep_pattern = keyword
        if sender:
            # Search lines matching both sender_name and keyword
            grep_cmd = (
                f'grep -rn --include="*.jsonl" {_shell_quote(sender)} {_shell_quote(search_path)} '
                f'| grep -i {_shell_quote(keyword)}'
            )
        elif find_name_filter:
            grep_cmd = (
                f'find {_shell_quote(group_dir)} {find_name_filter} -name "*.jsonl" '
                f'-exec grep -Hn -i {_shell_quote(grep_pattern)} {{}} +'
            )
        else:
            grep_cmd = (
                f'grep -rn -i --include="*.jsonl" {_shell_quote(grep_pattern)} '
                f'{_shell_quote(search_path)}'
            )

        # Limit output to avoid flooding
        grep_cmd += ' | head -50'

        try:
            result = subprocess.run(
                grep_cmd, shell=True, capture_output=True, text=True,
                timeout=10, cwd=self.base_dir,
            )
            output = result.stdout.strip()
            if not output:
                return f'No results found for "{keyword}" in group {group_id}.'

            # Format output for readability
            return self._format_output(output, group_dir)
        except subprocess.TimeoutExpired:
            return 'Search timed out. Try narrowing the date range or keyword.'
        except Exception as e:
            return f'Search failed: {e}'

    def _format_output(self, raw_output: str, group_dir: str) -> str:
        """Clean up grep output into a readable format."""
        import json as _json

        lines = raw_output.split('\n')
        results = []
        for line in lines[:50]:
            # Try to parse the JSONL content from grep output
            # Format: filepath:linenum:json_content
            colon_idx = line.find('.jsonl:')
            if colon_idx == -1:
                results.append(line)
                continue
            json_part = line[colon_idx + 7:]  # after ".jsonl:"
            # Skip the line number part
            colon2 = json_part.find(':')
            if colon2 != -1:
                json_part = json_part[colon2 + 1:]
            try:
                msg = _json.loads(json_part)
                ts = time.strftime('%Y-%m-%d %H:%M',
                                   time.localtime(msg.get('timestamp', 0)))
                name = msg.get('sender_name', '?')
                content = msg.get('content', '')
                results.append(f'[{ts}] {name}: {content}')
            except (_json.JSONDecodeError, TypeError):
                results.append(line)

        header = f'Found {len(results)} result(s):\n'
        return header + '\n'.join(results)


def _shell_quote(s: str) -> str:
    """Simple shell quoting to prevent injection."""
    import shlex
    return shlex.quote(s)
