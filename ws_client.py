#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

# ---------------------------------------------------------------------------
# Path setup – ensure the project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# AgentUniverse bootstrap (must happen before importing agent classes)
# ---------------------------------------------------------------------------
from agentuniverse.base.agentuniverse import AgentUniverse
from agentuniverse.agent.agent_manager import AgentManager

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage

CONFIG_PATH = os.path.join(PROJECT_ROOT, 'qq_social_bot_app', 'config', 'config.toml')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8082
EXPECTED_PATH = "/ws/onebot"

BOT_ID = 'bot_self'
BOT_NAMES = ['米浴']
BOT_QQ_ID: int | None = None  # auto-detected from first event's self_id

logger = logging.getLogger(__name__)

# Thread pool for running synchronous agent.run() without blocking the event loop
_executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_group_message(evt: dict) -> bool:
    """Only accept group chat messages."""
    return (evt.get("post_type") == "message"
            and evt.get("message_type") == "group")


def parse_segments(segments: Any, bot_self_id: int | None) -> tuple[str, list[str], list[str], str]:
    """Parse OneBot message segments into structured data.

    Returns:
        (text_content, at_list, image_urls, reply_to_message_id)

    - text_content:  plain text with image placeholders like [图片]
    - at_list:       list of user IDs being @'d (str); bot's self_id mapped to BOT_ID
    - image_urls:    list of image HTTP URLs for multimodal
    - reply_to:      message_id of the message being replied to, or ''
    """
    if not isinstance(segments, list):
        return (str(segments) if segments else ''), [], [], ''

    text_parts: list[str] = []
    at_list: list[str] = []
    image_urls: list[str] = []
    reply_to = ''

    for seg in segments:
        if not isinstance(seg, dict):
            text_parts.append(str(seg))
            continue

        seg_type = seg.get("type")
        data = seg.get("data", {}) or {}

        if seg_type == "text":
            text_parts.append(data.get("text", ""))

        elif seg_type == "at":
            qq = data.get("qq", "")
            if qq:
                # Map bot's own QQ id to the logical BOT_ID
                if bot_self_id and str(qq) == str(bot_self_id):
                    at_list.append(BOT_ID)
                else:
                    at_list.append(str(qq))
                text_parts.append(f"@{qq}")

        elif seg_type == "image":
            url = data.get("url", "")
            if url:
                image_urls.append(url)
            text_parts.append("[图片]")

        elif seg_type == "reply":
            reply_to = str(data.get("id", ""))
            # Don't append text placeholder for reply – it's metadata

        elif seg_type == "file":
            text_parts.append("[文件]")

        else:
            text_parts.append(f"[{seg_type}]")

    return "".join(text_parts).strip(), at_list, image_urls, reply_to


def evt_to_group_message(evt: dict, bot_self_id: int | None) -> tuple[GroupMessage, list[str]]:
    """Convert a OneBot group message event to (GroupMessage, image_urls).

    The image_urls are returned separately because GroupMessage.content is str-only,
    while the agent framework accepts image_urls as a separate parameter for
    multimodal LLM calls.
    """
    segments = evt.get("message", [])
    text, at_list, image_urls, reply_to = parse_segments(segments, bot_self_id)

    # Fallback: if segment parsing produced empty text, use raw_message
    if not text:
        text = evt.get("raw_message", "") or ""

    sender = evt.get("sender", {}) or {}
    # Prefer card (group nickname) over nickname
    sender_name = sender.get("card") or sender.get("nickname") or str(evt.get("user_id", ""))

    msg = GroupMessage(
        content=text,
        sender_id=str(evt.get("user_id", "")),
        sender_name=sender_name,
        group_id=str(evt.get("group_id", "")),
        timestamp=float(evt.get("time", 0)),
        message_id=str(evt.get("message_id", "")),
        reply_to=reply_to,
        at_list=at_list,
    )
    return msg, image_urls


def run_agent_sync(agent, messages: list[GroupMessage],
                   image_urls: list[str]) -> str:
    """Call the agent synchronously (designed to be run in a thread pool)."""
    kwargs = dict(
        messages=messages,
        bot_id=BOT_ID,
        bot_names=BOT_NAMES,
    )
    if image_urls:
        kwargs['image_urls'] = image_urls

    output = agent.run(**kwargs)
    return output.get_data('output', '')


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def handler(websocket) -> None:
    global BOT_QQ_ID

    remote = getattr(websocket, "remote_address", None)
    path = websocket.request.path

    if path != EXPECTED_PATH:
        await websocket.close(code=1008, reason="Invalid path")
        return

    print(f"[{now_str()}] WS connected: remote={remote}, path={path}")

    # Lazy-load agent (already initialised in main)
    agent = AgentManager().get_instance_obj('qq_social_agent')
    if agent is None:
        print(f"[{now_str()}] ERROR: qq_social_agent not loaded. Check YAML config.")
        return

    loop = asyncio.get_running_loop()

    try:
        async for raw_message in websocket:
            try:
                evt = json.loads(raw_message)
            except json.JSONDecodeError:
                continue

            # Auto-detect bot's QQ ID from the first event
            if BOT_QQ_ID is None and "self_id" in evt:
                BOT_QQ_ID = evt["self_id"]
                print(f"[{now_str()}] Bot QQ ID detected: {BOT_QQ_ID}")

            # Only process group messages
            if not is_group_message(evt):
                continue

            msg, image_urls = evt_to_group_message(evt, BOT_QQ_ID)

            print(f"[{now_str()}] [GROUP] "
                  f"group={msg.group_id} "
                  f"user={msg.sender_id}({msg.sender_name}) "
                  f"text={msg.content}"
                  f"{' images=' + str(len(image_urls)) if image_urls else ''}")

            # Run agent in thread pool to avoid blocking the WS event loop
            try:
                response = await loop.run_in_executor(
                    _executor,
                    run_agent_sync, agent, [msg], image_urls,
                )
            except Exception:
                logger.exception("Agent execution failed")
                continue

            if response:
                print(f"[{now_str()}] [BOT REPLY] {response}")
                # TODO: send response back to QQ group via OneBot API
            else:
                print(f"[{now_str()}] [BOT] (no reply)")

    except ConnectionClosed as e:
        print(f"[{now_str()}] WS disconnected: code={e.code}, reason={e.reason}")
    except Exception as e:
        print(f"[{now_str()}] Unexpected error: {e!r}")
        logger.exception("Handler error")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print(f"[{now_str()}] Initialising AgentUniverse...")
    AgentUniverse().start(config_path=CONFIG_PATH)
    print(f"[{now_str()}] Agent framework ready.")

    print(f"[{now_str()}] Starting WS server at ws://{HOST}:{PORT}{EXPECTED_PATH}")
    async with serve(handler, HOST, PORT):
        print(f"[{now_str()}] Waiting for NapCat reverse WS connection...")
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    asyncio.run(main())
