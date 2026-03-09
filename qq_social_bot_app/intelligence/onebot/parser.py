"""OneBot message parsing — pure functions, no state."""

from typing import Any

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage


def is_group_message(evt: dict) -> bool:
    return evt.get("post_type") == "message" and evt.get("message_type") == "group"


def is_private_message(evt: dict) -> bool:
    return evt.get("post_type") == "message" and evt.get("message_type") == "private"


def parse_segments(segments: Any, bot_self_id: int | None,
                   bot_id: str = 'bot_self') -> tuple[str, list[str], list[str], str]:
    """Extract text, @mentions, image URLs, and reply info from OneBot message segments.

    Args:
        segments: OneBot message segment list.
        bot_self_id: The bot's QQ numeric ID (for detecting @bot).
        bot_id: Logical bot identifier string (e.g. 'bot_self').
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
                if bot_self_id and str(qq) == str(bot_self_id):
                    at_list.append(bot_id)
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


def evt_to_group_message(evt: dict, bot_self_id: int | None,
                         bot_id: str = 'bot_self') -> GroupMessage:
    segments = evt.get("message", [])
    text, at_list, image_urls, reply_to = parse_segments(segments, bot_self_id, bot_id)

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


def evt_to_private_message(evt: dict, bot_self_id: int | None,
                           bot_id: str = 'bot_self') -> GroupMessage:
    segments = evt.get("message", [])
    text, at_list, image_urls, reply_to = parse_segments(segments, bot_self_id, bot_id)

    if not text:
        text = evt.get("raw_message", "") or ""

    sender = evt.get("sender", {}) or {}
    sender_name = sender.get("nickname") or str(evt.get("user_id", ""))
    user_id = str(evt.get("user_id", ""))

    if bot_id not in at_list:
        at_list.append(bot_id)

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
