"""Builders for synthetic Claude Code transcripts.

Tests construct transcripts rather than shipping captured ones — real
transcripts contain real prompts, and a fixture that has to be scrubbed before
it can be committed is a fixture nobody will update.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

BASE_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def ts(offset_seconds: int = 0) -> str:
    return (
        (BASE_TIME + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")
    )


def assistant_turn(
    *,
    session: str = "sess-1",
    model: str = "claude-opus-5",
    input_tokens: int = 100,
    cache_write_5m: int = 0,
    cache_write_1h: int = 0,
    cache_read: int = 0,
    output_tokens: int = 50,
    offset: int = 0,
    message_id: str | None = None,
    sidechain: bool = False,
    tool_uses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "ok"}]
    for use in tool_uses or []:
        content.append({"type": "tool_use", **use})
    return {
        "type": "assistant",
        "sessionId": session,
        "timestamp": ts(offset),
        "isSidechain": sidechain,
        "message": {
            "id": message_id or f"msg_{session}_{offset}",
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": cache_write_5m,
                    "ephemeral_1h_input_tokens": cache_write_1h,
                },
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
            },
        },
    }


def user_prompt(
    text: str, *, session: str = "sess-1", offset: int = 0, meta: bool = False
) -> dict[str, Any]:
    record = {
        "type": "user",
        "sessionId": session,
        "timestamp": ts(offset),
        "message": {"role": "user", "content": text},
    }
    if meta:
        record["isMeta"] = True
    return record


def tool_result(
    tool_use_id: str, text: str, *, session: str = "sess-1", offset: int = 0
) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": ts(offset),
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ],
        },
    }


def write_transcript(
    root: Path, project: str, session: str, records: list[dict[str, Any]]
) -> Path:
    """Write ``records`` to ``root/<project-slug>/<session>.jsonl``."""
    directory = root / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


@pytest.fixture
def since():
    return BASE_TIME - timedelta(days=1)


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root
