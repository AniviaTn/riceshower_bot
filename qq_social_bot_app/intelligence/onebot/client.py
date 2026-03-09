"""OneBotWSClient — WebSocket communication with OneBot protocol."""

import asyncio
import json
import uuid

from .parser import build_onebot_message


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
