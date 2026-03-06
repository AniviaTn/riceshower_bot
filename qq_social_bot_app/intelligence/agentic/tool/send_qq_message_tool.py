# -*- coding: utf-8 -*-
"""send_qq_message tool – lets the agent send QQ messages via OneBot."""

import logging
import time
from typing import Optional

from agentuniverse.agent.action.tool.tool import Tool

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage

logger = logging.getLogger(__name__)


class SendQQMessageTool(Tool):
    """Send a QQ message to the current conversation (group or private).

    Requires ``agent_context.extra`` to contain:
      - onebot_client: OneBotWSClient instance
      - send_scene: "group" | "private"
      - send_target_group_id: str (for group scene)
      - send_target_user_id: str (for private scene)
      - bot_message_ids: deque (for tracking sent message ids)
      - bot_qq_id: str (bot's own QQ ID)
      - bot_name: str (bot's display name)
      - memory: QQSocialMemory instance (optional, for recording bot messages)
    """

    require_agent_context: bool = True

    async def async_execute(
        self,
        text: str,
        reply_to: Optional[str] = None,
        at_user_ids: Optional[str] = None,
        agent_context=None,
    ) -> str:
        if not agent_context:
            return "Error: no agent_context provided."

        extra = agent_context.extra
        onebot = extra.get("onebot_client")
        if onebot is None:
            return "Error: onebot_client not available."

        scene = extra.get("send_scene", "group")
        group_id = extra.get("send_target_group_id")
        user_id = extra.get("send_target_user_id")

        # Parse at_user_ids from comma-separated string
        at_list: list[str] | None = None
        if at_user_ids:
            at_list = [uid.strip() for uid in at_user_ids.split(",") if uid.strip()]

        try:
            if scene == "group":
                if not group_id:
                    return "Error: group_id not available."
                resp = await onebot.send_group_msg(
                    group_id=group_id,
                    text=text,
                    reply_to=reply_to,
                    at_user_ids=at_list,
                )
            else:
                if not user_id:
                    return "Error: user_id not available."
                resp = await onebot.send_private_msg(
                    user_id=user_id,
                    text=text,
                    reply_to=reply_to,
                )
        except Exception as e:
            logger.exception("Failed to send QQ message")
            return f"Error sending message: {e}"

        # Track bot message id
        mid = _extract_message_id(resp)
        if mid:
            bot_message_ids = extra.get("bot_message_ids")
            if bot_message_ids is not None:
                bot_message_ids.append(mid)

        # Record bot's own message into WorkingMemory + RawMessageStore
        await self._record_bot_message(extra, scene, group_id, user_id, text, mid)

        return "Message sent successfully."

    @staticmethod
    async def _record_bot_message(extra: dict, scene: str,
                                   group_id: str | None,
                                   user_id: str | None,
                                   text: str, message_id: str | None) -> None:
        """Write the bot's outgoing message into memory stores."""
        memory = extra.get("memory")
        if memory is None:
            return

        bot_qq_id = extra.get("bot_qq_id", "bot")
        bot_name = extra.get("bot_name", "bot")

        if scene == "group":
            target_group = group_id or ""
        else:
            target_group = f"private_{user_id}" if user_id else ""

        if not target_group:
            return

        msg = GroupMessage(
            content=text,
            sender_id=str(bot_qq_id),
            sender_name=bot_name,
            group_id=target_group,
            timestamp=time.time(),
            message_id=message_id or "",
        )

        try:
            await memory.async_add_group_message(msg)
        except Exception:
            logger.debug("Failed to record bot message", exc_info=True)

    def execute(self, **kwargs):
        raise NotImplementedError(
            "SendQQMessageTool is async-only. Use async_execute()."
        )


def _extract_message_id(resp: dict) -> str | None:
    """Extract message_id from OneBot send response."""
    if resp.get("status") == "ok":
        data = resp.get("data") or {}
        mid = data.get("message_id")
        if mid is not None:
            return str(mid)
    return None
