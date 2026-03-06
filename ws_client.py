#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import os

os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')
import sys
import uuid
from collections import deque
from datetime import datetime
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from agentuniverse.base.agentuniverse import AgentUniverse
from agentuniverse.agent.agent_manager import AgentManager
from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.memory_services import (
    init_services, get_id_mapping_service, get_scheduler_service,
)
from qq_social_bot_app.intelligence.scheduler.jobs import (
    summarize_all_groups, startup_check,
)

CONFIG_PATH = os.path.join(PROJECT_ROOT, 'qq_social_bot_app', 'config', 'config.toml')

HOST = "127.0.0.1"
PORT = 8082
EXPECTED_PATH = "/ws/onebot"

BOT_ID = 'bot_self'
BOT_NAMES = ['米浴', '米宝', '神の人形']
BOT_QQ_ID: int | None = None

PERIODIC_CHECK_INTERVAL = 120  # seconds between periodic checks for buffered messages

# Max bot message_ids to track (prevents unbounded growth)
_MAX_BOT_MESSAGE_IDS = 500

logger = logging.getLogger(__name__)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_group_message(evt: dict) -> bool:
    return evt.get("post_type") == "message" and evt.get("message_type") == "group"


def is_private_message(evt: dict) -> bool:
    return evt.get("post_type") == "message" and evt.get("message_type") == "private"


def parse_segments(segments: Any, bot_self_id: int | None) -> tuple[str, list[str], list[str], str]:
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

        elif seg_type == "file":
            text_parts.append("[文件]")

        else:
            text_parts.append(f"[{seg_type}]")

    return "".join(text_parts).strip(), at_list, image_urls, reply_to


def evt_to_group_message(evt: dict, bot_self_id: int | None) -> GroupMessage:
    segments = evt.get("message", [])
    text, at_list, image_urls, reply_to = parse_segments(segments, bot_self_id)

    if not text:
        text = evt.get("raw_message", "") or ""

    sender = evt.get("sender", {}) or {}
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
        image_urls=image_urls,
    )
    return msg


def evt_to_private_message(evt: dict, bot_self_id: int | None) -> GroupMessage:
    segments = evt.get("message", [])
    text, at_list, image_urls, reply_to = parse_segments(segments, bot_self_id)

    if not text:
        text = evt.get("raw_message", "") or ""

    sender = evt.get("sender", {}) or {}
    sender_name = sender.get("nickname") or str(evt.get("user_id", ""))
    user_id = str(evt.get("user_id", ""))

    if BOT_ID not in at_list:
        at_list.append(BOT_ID)

    msg = GroupMessage(
        content=text,
        sender_id=user_id,
        sender_name=sender_name,
        group_id=f"private_{user_id}",
        timestamp=float(evt.get("time", 0)),
        message_id=str(evt.get("message_id", "")),
        reply_to=reply_to,
        at_list=at_list,
        image_urls=image_urls,
    )
    return msg


def build_onebot_message(
    text: str,
    reply_to: str | None = None,
    at_user_ids: list[str] | None = None,
) -> list[dict]:
    """构造 OneBot message segment 列表。"""
    segments: list[dict] = []

    if reply_to:
        segments.append({
            "type": "reply",
            "data": {"id": str(reply_to)}
        })

    if at_user_ids:
        for uid in at_user_ids:
            segments.append({
                "type": "at",
                "data": {"qq": str(uid)}
            })
            segments.append({
                "type": "text",
                "data": {"text": " "}
            })

    if text:
        segments.append({
            "type": "text",
            "data": {"text": text}
        })

    return segments


def check_hard_rules(msg: GroupMessage, bot_id: str, bot_names: list[str],
                     bot_message_ids: set[str]) -> bool:
    """Check whether a group message triggers a hard rule requiring immediate reply.

    Rules (first match wins):
    1. bot_id in msg.at_list
    2. @BotName in content
    3. bot name mentioned in content (case-insensitive)
    4. msg.reply_to is a bot message_id (local set lookup)
    """
    content = msg.content or ''

    # Rule 1: bot was @'d via QQ at segment
    if bot_id and bot_id in (msg.at_list or []):
        return True

    # Rule 2: @BotName in text
    for name in bot_names:
        if f'@{name}' in content:
            return True

    # Rule 3: bot name mentioned in text (case-insensitive)
    content_lower = content.lower()
    for name in bot_names:
        if name.lower() in content_lower:
            return True

    # Rule 4: replying to a bot message
    if msg.reply_to and msg.reply_to in bot_message_ids:
        return True

    return False


class OneBotWSClient:
    """基于当前 reverse websocket 连接发送 OneBot action，并等待 echo 对应响应。"""

    def __init__(self, websocket):
        self.websocket = websocket
        self._pending: dict[str, asyncio.Future] = {}
        self.event_queue: asyncio.Queue = asyncio.Queue()

    def handle_action_response(self, data: dict) -> bool:
        echo = data.get("echo")
        status = data.get("status")
        if echo is None or status is None:
            return False

        fut = self._pending.pop(str(echo), None)
        if fut and not fut.done():
            fut.set_result(data)
        return True

    async def reader_loop(self):
        """持续读取 WebSocket 消息，分发 action 响应和事件。"""
        try:
            async for raw_message in self.websocket:
                try:
                    evt = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue
                if not self.handle_action_response(evt):
                    await self.event_queue.put(evt)
        finally:
            await self.event_queue.put(None)

    async def call_action(self, action: str, params: dict, timeout: float = 20.0) -> dict:
        echo = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut

        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }

        await self.websocket.send(json.dumps(payload, ensure_ascii=False))

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            return resp
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise TimeoutError(f"OneBot action timeout: {action}")

    async def send_group_msg(
        self,
        group_id: str | int,
        text: str,
        reply_to: str | None = None,
        at_user_ids: list[str] | None = None,
    ) -> dict:
        message = build_onebot_message(text=text, reply_to=reply_to, at_user_ids=at_user_ids)
        return await self.call_action(
            "send_group_msg",
            {
                "group_id": int(group_id),
                "message": message,
            }
        )

    async def send_private_msg(
        self,
        user_id: str | int,
        text: str,
        reply_to: str | None = None,
    ) -> dict:
        message = build_onebot_message(text=text, reply_to=reply_to)
        return await self.call_action(
            "send_private_msg",
            {
                "user_id": int(user_id),
                "message": message,
            }
        )


async def run_agent_async(agent, messages: list[GroupMessage],
                          must_reply: bool = True,
                          onebot_client=None, send_scene: str = "group",
                          trigger_message_id: str | None = None,
                          bot_message_ids=None) -> str:
    kwargs = dict(
        messages=messages,
        bot_id=BOT_ID,
        bot_names=BOT_NAMES,
        must_reply=must_reply,
        onebot_client=onebot_client,
        send_scene=send_scene,
        trigger_message_id=trigger_message_id,
        bot_message_ids=bot_message_ids,
    )

    output = await agent.async_run(**kwargs)
    return output.get_data("output", "")


async def _handle_group_agent_call(
    agent, onebot: OneBotWSClient,
    group_id: str,
    group_buffers: dict[str, list[GroupMessage]],
    group_locks: dict[str, asyncio.Lock],
    bot_message_ids: deque,
    must_reply: bool,
) -> None:
    """Drain the buffer for a group and call agent."""
    lock = group_locks.setdefault(group_id, asyncio.Lock())
    async with lock:
        # Drain buffer
        messages = group_buffers.pop(group_id, [])

        if not messages:
            return

        # The trigger message is the last one (for reply_to targeting)
        trigger_msg = messages[-1]

        try:
            await run_agent_async(
                agent, messages, must_reply=must_reply,
                onebot_client=onebot, send_scene="group",
                trigger_message_id=trigger_msg.message_id,
                bot_message_ids=bot_message_ids,
            )
        except Exception:
            logger.exception("Agent execution failed for group %s", group_id)
            return


async def periodic_check(agent, onebot: OneBotWSClient,
                         group_buffers: dict[str, list[GroupMessage]],
                         group_locks: dict[str, asyncio.Lock],
                         bot_message_ids: deque) -> None:
    """Periodically check all groups with buffered messages and call agent with must_reply=False."""
    while True:
        await asyncio.sleep(PERIODIC_CHECK_INTERVAL)

        # Snapshot current group_ids that have buffered messages
        group_ids = list(group_buffers.keys())
        if not group_ids:
            continue

        print(f"[{now_str()}] [PERIODIC] Checking {len(group_ids)} group(s) with buffered messages")

        for group_id in group_ids:
            if group_id not in group_buffers or not group_buffers[group_id]:
                continue
            try:
                await _handle_group_agent_call(
                    agent, onebot, group_id,
                    group_buffers, group_locks,
                    bot_message_ids, must_reply=False,
                )
            except Exception:
                logger.exception("Periodic check failed for group %s", group_id)


async def handler(websocket) -> None:
    global BOT_QQ_ID

    remote = getattr(websocket, "remote_address", None)
    path = websocket.request.path

    if path != EXPECTED_PATH:
        await websocket.close(code=1008, reason="Invalid path")
        return

    print(f"[{now_str()}] WS connected: remote={remote}, path={path}")

    agent = AgentManager().get_instance_obj('qq_social_agent')
    if agent is None:
        print(f"[{now_str()}] ERROR: qq_social_agent not loaded. Check YAML config.")
        return

    onebot = OneBotWSClient(websocket)

    # Per-connection state
    group_buffers: dict[str, list[GroupMessage]] = {}
    group_locks: dict[str, asyncio.Lock] = {}
    bot_message_ids: deque[str] = deque(maxlen=_MAX_BOT_MESSAGE_IDS)

    # Start reader and periodic check coroutines
    reader_task = asyncio.create_task(onebot.reader_loop())
    periodic_task = asyncio.create_task(
        periodic_check(agent, onebot, group_buffers,
                       group_locks, bot_message_ids)
    )

    try:
        while True:
            try:
                evt = await onebot.event_queue.get()
            except asyncio.CancelledError:
                break
            if evt is None:
                break

            if BOT_QQ_ID is None and "self_id" in evt:
                BOT_QQ_ID = evt["self_id"]
                print(f"[{now_str()}] Bot QQ ID detected: {BOT_QQ_ID}")

            # ---- Private message: direct agent call, must_reply=True ----
            if is_private_message(evt):
                msg = evt_to_private_message(evt, BOT_QQ_ID)
                print(f"[{now_str()}] [PRIVATE] user={msg.sender_id}({msg.sender_name}) text={msg.content}")

                # Update ID mappings
                try:
                    id_svc = get_id_mapping_service()
                    id_svc.set_user_name(msg.sender_id, msg.sender_name)
                except Exception:
                    pass  # Not critical

                try:
                    await run_agent_async(
                        agent, [msg], must_reply=True,
                        onebot_client=onebot, send_scene="private",
                        trigger_message_id=msg.message_id,
                        bot_message_ids=bot_message_ids,
                    )
                except Exception:
                    logger.exception("Agent execution failed (private)")
                    continue
                continue

            # ---- Group message: buffer + hard rule check ----
            if is_group_message(evt):
                msg = evt_to_group_message(evt, BOT_QQ_ID)
                group_id = msg.group_id
                print(f"[{now_str()}] [GROUP] group={group_id} user={msg.sender_id}({msg.sender_name}) text={msg.content}")

                # Update ID mappings
                try:
                    id_svc = get_id_mapping_service()
                    id_svc.set_user_name(msg.sender_id, msg.sender_name)
                except Exception:
                    pass  # Not critical

                # Buffer the message
                group_buffers.setdefault(group_id, []).append(msg)

                # Check hard rules
                if check_hard_rules(msg, BOT_ID, BOT_NAMES, set(bot_message_ids)):
                    print(f"[{now_str()}] [HARD RULE] Triggered for group={group_id}")
                    asyncio.create_task(
                        _handle_group_agent_call(
                            agent, onebot, group_id,
                            group_buffers, group_locks,
                            bot_message_ids, must_reply=True,
                        )
                    )
                continue

    except ConnectionClosed as e:
        print(f"[{now_str()}] WS disconnected: code={e.code}, reason={e.reason}")
    except Exception as e:
        print(f"[{now_str()}] Unexpected error: {e!r}")
        logger.exception("Handler error")
    finally:
        periodic_task.cancel()
        reader_task.cancel()


async def main() -> None:
    print(f"[{now_str()}] Initialising AgentUniverse...")
    AgentUniverse().start(config_path=CONFIG_PATH)
    print(f"[{now_str()}] Agent framework ready.")

    print(f"[{now_str()}] Initialising memory services...")
    init_services()
    print(f"[{now_str()}] Memory services ready.")

    # Start scheduler and register periodic jobs
    print(f"[{now_str()}] Starting scheduler...")
    scheduler = get_scheduler_service()
    scheduler.start()

    scheduler.add_cron_job(
        job_id='summarize_all_groups',
        func=summarize_all_groups,
        cron_expr='0 */4 * * *',
    )
    print(f"[{now_str()}] Scheduler ready. Jobs: {scheduler.list_jobs()}")

    # Non-blocking startup check for stale groups
    asyncio.create_task(startup_check())

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
