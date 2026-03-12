# !/usr/bin/env python3
# -*- coding:utf-8 -*-
# @Time    : 2026/2/22
# @FileName: zenmux_web_search_tool.py
from typing import Optional, Dict, List

import httpx
from pydantic import Field
from agentuniverse.agent.action.tool.tool import Tool, ToolInput
from agentuniverse.base.util.env_util import get_from_env

ZENMUX_ANTHROPIC_API_URL = "https://zenmux.ai/api/anthropic/v1/messages"


class ZenmuxWebSearchTool(Tool):
    """ZenMux web search tool.

    Uses the ZenMux Anthropic Messages API with web_search_20250305 tool
    to perform real-time web searches.

    Note:
        You need a valid ZenMux API key. Set the ZENMUX_API_KEY environment variable.
    """

    zenmux_api_key: Optional[str] = Field(default_factory=lambda: get_from_env("ZENMUX_API_KEY"))
    zenmux_model: Optional[str] = Field(default="anthropic/claude-sonnet-4.6")
    zenmux_base_url: Optional[str] = Field(default=ZENMUX_ANTHROPIC_API_URL)
    max_uses: Optional[int] = Field(default=3)
    user_location: Optional[Dict[str, str]] = Field(default_factory=lambda: {
        "type": "approximate",
        "country": "CN",
        "timezone": "Asia/Shanghai",
    })
    allowed_domains: Optional[List[str]] = Field(default=None)
    blocked_domains: Optional[List[str]] = Field(default=None)

    def _build_request(self, query: str, search_context_size: str = "medium") -> tuple:
        """Build the HTTP request headers and payload for ZenMux Anthropic Messages API."""
        headers = {
            "x-api-key": self.zenmux_api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        web_search_tool = {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": self.max_uses,
        }
        if self.user_location:
            web_search_tool["user_location"] = self.user_location
        if self.allowed_domains:
            web_search_tool["allowed_domains"] = self.allowed_domains
        if self.blocked_domains:
            web_search_tool["blocked_domains"] = self.blocked_domains

        payload = {
            "model": self.zenmux_model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": query
                }
            ],
            "tools": [web_search_tool],
        }
        return headers, payload

    def _parse_results(self, response_json: dict) -> str:
        """Parse ZenMux Anthropic Messages API response and extract search results."""
        content_blocks = response_json.get("content", [])
        if not content_blocks:
            return "No web search result was found."

        text_parts = []
        citations = []

        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "web_search_tool_result":
                results = block.get("content", [])
                if isinstance(results, list):
                    for result in results:
                        if result.get("type") == "web_search_result":
                            title = result.get("title", "")
                            url = result.get("url", "")
                            if title and url:
                                citations.append(f"[{title}]({url})")

        result_parts = []
        if text_parts:
            result_parts.append("\n".join(text_parts))
        if citations:
            result_parts.append("\n\nSources:\n" + "\n".join(citations))

        if not result_parts:
            return "No web search result was found."
        return "".join(result_parts)

    def execute(self, input: str, search_context_size: str = "medium"):
        """Execute web search via ZenMux Anthropic Messages API synchronously."""
        headers, payload = self._build_request(input, search_context_size)
        response = httpx.post(
            self.zenmux_base_url,
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        return self._parse_results(response.json())

    async def async_execute(self, input: str, search_context_size: str = "medium"):
        """Execute web search via ZenMux Anthropic Messages API asynchronously."""
        headers, payload = self._build_request(input, search_context_size)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.zenmux_base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return self._parse_results(response.json())
