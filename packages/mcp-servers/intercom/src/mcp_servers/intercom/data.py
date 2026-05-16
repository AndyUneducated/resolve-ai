"""Deterministic in-memory fake data backing the Intercom MCP server.

Surface used by Technical Agent to pull conversation context and to tag the
user for state changes (e.g. `awaiting-eng`, `kb-followup-required`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Conversation:
    id: str
    user_id: str
    subject: str
    messages: list[str] = field(default_factory=list)
    created_at: str = "2026-04-01T00:00:00Z"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class User:
    id: str
    email: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Store:
    conversations: dict[str, Conversation] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)


def _seed() -> _Store:
    store = _Store()
    convs = [
        Conversation(
            "ic_001",
            "u_demo_001",
            subject="502 on /v1/events",
            messages=[
                "Customer: getting intermittent 502s",
                "Agent: can you share request id?",
            ],
        ),
        Conversation(
            "ic_002",
            "u_demo_002",
            subject="Webhook signature mismatch",
            messages=["Customer: signature verification fails"],
        ),
    ]
    for c in convs:
        store.conversations[c.id] = c
    users = [
        User("u_demo_001", "ada@example.com", tags=["beta"]),
        User("u_demo_002", "kai@example.com"),
    ]
    for u in users:
        store.users[u.id] = u
    return store


STORE = _seed()


def reset_store() -> None:
    """Restore deterministic state. Used by tests / between runs."""
    global STORE
    STORE = _seed()


def get_conversation(conversation_id: str) -> dict[str, Any]:
    conv = STORE.conversations.get(conversation_id)
    if conv is None:
        raise KeyError(f"conversation_not_found: {conversation_id}")
    return conv.to_dict()


def tag_user(user_id: str, tag: str) -> dict[str, Any]:
    user = STORE.users.get(user_id)
    if user is None:
        raise KeyError(f"user_not_found: {user_id}")
    if not tag or not tag.strip():
        raise ValueError("tag_requires_value")
    if tag in user.tags:
        raise ValueError(f"already_tagged: {tag}")
    user.tags.append(tag)
    return user.to_dict()
