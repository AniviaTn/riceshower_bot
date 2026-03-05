# !/usr/bin/env python3
# -*- coding:utf-8 -*-
# @Time    : 2026/2/22
# @FileName: zenmux_web_search_tool.py
from typing import Optional

import httpx
from pydantic import Field
from agentuniverse.agent.action.tool.tool import Tool, ToolInput
from agentuniverse.base.util.env_util import get_from_env

ZENMUX_API_URL = "https://zenmux.ai/api/v1/chat/completions"


class ZenmuxWebSearchTool(Tool):
    """ZenMux web search tool.

    Uses the ZenMux API with web_search_options to perform real-time web searches.
    The tool sends a query to ZenMux, which leverages the model's built-in web search
    capability (via the Chat Completions protocol) to retrieve up-to-date information.

    Note:
        You need a valid ZenMux API key. Set the ZENMUX_API_KEY environment variable.
    """

    zenmux_api_key: Optional[str] = Field(default_factory=lambda: get_from_env("ZENMUX_API_KEY"))
    zenmux_model: Optional[str] = Field(default="openai/gpt-4o-mini")
    zenmux_base_url: Optional[str] = Field(default=ZENMUX_API_URL)
    search_context_size: Optional[str] = Field(default="medium")

    def _build_request(self, query: str) -> tuple:
        """Build the HTTP request headers and payload for ZenMux web search."""
        headers = {
            "Authorization": f"Bearer {self.zenmux_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.zenmux_model,
            "messages": [
                {
                    "role": "user",
                    "content": query
                }
            ],
            "web_search_options": {
                "search_context_size": self.search_context_size,
            }
        }
        return headers, payload

    def _parse_results(self, response_json: dict) -> str:
        """Parse ZenMux API response and extract search results with citations."""
        choices = response_json.get("choices", [])
        if not choices:
            return "No web search result was found."

        message = choices[0].get("message", {})
        content = message.get("content", "")

        # Extract URL citations from annotations if available
        annotations = message.get("annotations", [])
        citations = []
        for annotation in annotations:
            if annotation.get("type") == "url_citation":
                title = annotation.get("title", "")
                url = annotation.get("url", "")
                if title and url:
                    citations.append(f"[{title}]({url})")

        result_parts = []
        if content:
            result_parts.append(content)
        if citations:
            result_parts.append("\n\nSources:\n" + "\n".join(citations))

        if not result_parts:
            return "No web search result was found."
        return "".join(result_parts)

    def execute(self, input: str):
        """Execute web search via ZenMux API synchronously."""
        headers, payload = self._build_request(input)
        response = httpx.post(
            self.zenmux_base_url,
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        return self._parse_results(response.json())

    async def async_execute(self, input: str):
        """Execute web search via ZenMux API asynchronously."""
        headers, payload = self._build_request(input)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.zenmux_base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return self._parse_results(response.json())
