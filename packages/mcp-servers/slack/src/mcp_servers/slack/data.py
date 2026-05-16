"""Deterministic in-memory fake data backing the Slack MCP server.

Used by Escalation Agent to "notify on-call" and by Technical Agent to drop
internal coordination messages. No real Slack tokens involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    channel: str
    text: str
    mention: str | None = None
    posted_at: str = "2026-04-01T00:00:00Z"

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "text": self.text,
            "mention": self.mention,
            "posted_at": self.posted_at,
        }


@dataclass
class _Store:
    channels: set[str] = field(
        default_factory=lambda: {"#oncall-billing", "#oncall-technical", "#general"}
    )
    messages: list[Message] = field(default_factory=list)


STORE = _Store()


def reset_store() -> None:
    """Restore deterministic state. Used by tests / between runs."""
    global STORE
    STORE = _Store()


def _check_channel(channel: str) -> None:
    if not channel.startswith("#"):
        raise ValueError(f"invalid_channel: {channel!r} (must start with '#')")
    if channel not in STORE.channels:
        raise KeyError(f"channel_not_found: {channel}")


def notify_team(
    channel: str, message: str, mention: str | None = None
) -> dict[str, Any]:
    """Post a notification to an on-call channel with optional @-mention."""
    _check_channel(channel)
    if not message or not message.strip():
        raise ValueError("notify_requires_message")
    if mention is not None and not mention.startswith("@"):
        raise ValueError(f"invalid_mention: {mention!r} (must start with '@')")
    rendered = f"{mention} {message}" if mention else message
    msg = Message(channel=channel, text=rendered, mention=mention)
    STORE.messages.append(msg)
    return msg.to_dict()


def post_message(channel: str, message: str) -> dict[str, Any]:
    """Generic channel post (no @-mention)."""
    _check_channel(channel)
    if not message or not message.strip():
        raise ValueError("post_requires_message")
    msg = Message(channel=channel, text=message)
    STORE.messages.append(msg)
    return msg.to_dict()
