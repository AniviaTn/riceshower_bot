"""Data models for the QQ Social Bot.

Only contains the GroupMessage dataclass – the standard input format
for group chat messages used across the system.
"""
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class GroupMessage:
    """Group chat message object - standard input format for the agent."""
    content: str
    sender_id: str
    sender_name: str
    group_id: str
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    reply_to: str = ''
    at_list: list = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'content': self.content,
            'sender_id': self.sender_id,
            'sender_name': self.sender_name,
            'group_id': self.group_id,
            'timestamp': self.timestamp,
            'message_id': self.message_id,
            'reply_to': self.reply_to,
            'at_list': self.at_list,
            'image_urls': self.image_urls,
        }
