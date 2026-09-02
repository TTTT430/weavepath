"""Shared event vocabulary for Chat SSE and Agent Run journals.

Both surfaces retain their transport-specific envelopes, but use the same
names and payload conventions so a client can render one activity timeline.
"""
from __future__ import annotations

from typing import Any

EVENT_SCHEMA_VERSION = 1
EVENT_TYPES = frozenset({
    "message.started", "message.delta", "message.completed", "message.failed", "message.cancelled",
    "run.created", "run.started", "run.completed", "run.failed", "run.interrupted",
    "context.frozen", "model.started", "model.completed", "model.failed",
    "tool.requested", "tool.started", "tool.completed", "tool.failed",
    "approval.required",
})


def validate_event_type(event_type: str) -> str:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown runtime event type: {event_type}")
    return event_type


def event_payload(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a transport-neutral payload while preserving legacy fields."""
    validate_event_type(event_type)
    return {"schemaVersion": EVENT_SCHEMA_VERSION, **(payload or {})}
