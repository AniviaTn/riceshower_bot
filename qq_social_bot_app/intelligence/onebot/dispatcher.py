"""Message dispatch and Agent invocation logic."""

import asyncio
import logging
from collections import deque
from datetime import datetime

from agentuniverse.agent.agent_manager import AgentManager
from websockets.exceptions import ConnectionClosed

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage
from qq_social_bot_app.intelligence.social_memory.memory_services import get_id_mapping_service
from qq_social_bot_app.intelligence.social_memory.image_cache import download_images
from qq_social_bot_app.intelligence.utils import bot_config

from .client import OneBotWSClient
from .parser import (
    is_group_message, is_private_message,
    evt_to_group_message, evt_to_private_message,
)

logger = logging.getLogger(__name__)

# Max bot message_ids to track (prevents unbounded growth)
_MAX_BOT_MESSAGE_IDS = 500

# Detected QQ numeric ID of the bot (set on first event)
BOT_QQ_ID: int | None = None


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check_hard_rules(msg: GroupMessage, bot_id: str, bot_names: list[str],
                     bot_message_ids: set[str]) -> bool:
    """Check whether a group message triggers a hard rule requiring immediate reply."""
    content = msg.content or ''

    if bot_id and bot_id in (msg.at_list or []):
        return True

    for name in bot_names:
        if f'@{name}' in content:
            return True

    content_lower = content.lower()
    for name in bot_names:
        if name.lower() in content_lower:
            return True

    if msg.reply_to and msg.reply_to in bot_message_ids:
        return True

    return False


async def run_agent_async(agent, messages: list[GroupMessage],
                          must_reply: bool = True,
                          onebot_client=None, send_scene: str = "group",
                          trigger_message_id: str | None = None,
                          bot_message_ids=None) -> str:
    bot_id = bot_config.get_bot_id()
    bot_names = bot_config.get_bot_names()

    # Pre-download images before calling agent (Phase E)
    all_image_urls = []
    for m in messages:
        if hasattr(m, 'image_urls') and m.image_urls:
            all_image_urls.extend(m.image_urls)

    image_local_paths = []
    if all_image_urls:
        try:
            image_local_paths = await download_images(all_image_urls)
        except Exception:
            logger.warning('Image pre-download failed', exc_info=True)

    kwargs = dict(
        messages=messages,
        bot_id=bot_id,
        bot_names=bot_names,
        must_reply=must_reply,
        onebot_client=onebot_client,
        send_scene=send_scene,
        trigger_message_id=trigger_message_id,
        bot_message_ids=bot_message_ids,
    )

    if image_local_paths:
        kwargs['image_urls'] = image_local_paths

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
        messages = group_buffers.pop(group_id, [])

        if not messages:
            return

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
    interval = bot_config.get_periodic_check_interval()
    while True:
        await asyncio.sleep(interval)

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
    """Handle a single WebSocket connection from NapCat."""
    global BOT_QQ_ID

    remote = getattr(websocket, "remote_address", None)
    path = websocket.request.path
    ws_path = bot_config.get_ws_path()

    if path != ws_path:
        await websocket.close(code=1008, reason="Invalid path")
        return

    print(f"[{now_str()}] WS connected: remote={remote}, path={path}")

    agent = AgentManager().get_instance_obj('qq_social_agent')
    if agent is None:
        print(f"[{now_str()}] ERROR: qq_social_agent not loaded. Check YAML config.")
        return

    bot_id = bot_config.get_bot_id()
    bot_names = bot_config.get_bot_names()

    onebot = OneBotWSClient(websocket)

    # Per-connection state
    group_buffers: dict[str, list[GroupMessage]] = {}
    group_locks: dict[str, asyncio.Lock] = {}
    bot_message_ids: deque[str] = deque(maxlen=_MAX_BOT_MESSAGE_IDS)

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
                msg = evt_to_private_message(evt, BOT_QQ_ID, bot_id)
                print(f"[{now_str()}] [PRIVATE] user={msg.sender_id}({msg.sender_name}) text={msg.content}")

                try:
                    id_svc = get_id_mapping_service()
                    id_svc.set_user_name(msg.sender_id, msg.sender_name)
                except Exception:
                    pass

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
                msg = evt_to_group_message(evt, BOT_QQ_ID, bot_id)
                group_id = msg.group_id
                print(f"[{now_str()}] [GROUP] group={group_id} user={msg.sender_id}({msg.sender_name}) text={msg.content}")

                try:
                    id_svc = get_id_mapping_service()
                    id_svc.set_user_name(msg.sender_id, msg.sender_name)
                except Exception:
                    pass

                group_buffers.setdefault(group_id, []).append(msg)

                if check_hard_rules(msg, bot_id, bot_names, set(bot_message_ids)):
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
