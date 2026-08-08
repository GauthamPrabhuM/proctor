#!/usr/bin/env python3
"""Generate a synthetic transcript corpus that exercises every proctor finding.

Real transcripts contain real prompts, so they can't be committed or used for
screenshots. This builds a deterministic stand-in instead: same JSONL shape
Claude Code writes, entirely invented content, and a fixed seed so anyone
re-running it gets byte-identical output.

    python examples/generate_demo.py /tmp/demo-logs
    proctor /tmp/demo-logs --days 60

Every session below is engineered to trip a specific detector, so the resulting
report is a complete tour of what proctor looks for.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEED = 20260808
START = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
DAY = 24 * 60
"""Minutes in a day. Scenario bases are day-aligned so the chart spans weeks."""

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"
FABLE = "claude-fable-5"

# A block a developer might paste into every new session instead of putting it
# in CLAUDE.md — the thing the `repeat` detector is looking for.
HOUSE_RULES = """Our service conventions, please follow these exactly.
All handlers live under services/<name>/handlers and are registered in the
router module, never auto-discovered. Database access goes through the repo
layer; no ORM calls in handler code. Every public function needs a docstring
with an Args and Returns section. Errors are returned, never raised, across a
service boundary, and the error type must be one of the four in errors.py.
Tests live beside the code in a _test.py file and must not hit the network.
Migrations are forward-only and reviewed by the data team before merge.
Logging uses the structured logger; no bare print statements anywhere.
Feature flags are read once at startup and passed down, never read inline.
"""


def _stamp(offset_minutes: float) -> str:
    moment = START + timedelta(minutes=offset_minutes)
    return moment.isoformat().replace("+00:00", "Z")


def assistant(
    session: str,
    minute: float,
    model: str = OPUS,
    inp: int = 0,
    write_5m: int = 0,
    read: int = 0,
    out: int = 200,
    sidechain: bool = False,
    tool: dict | None = None,
    index: int = 0,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "Working on it."}]
    if tool:
        content.append({"type": "tool_use", **tool})
    return {
        "type": "assistant",
        "sessionId": session,
        "timestamp": _stamp(minute),
        "isSidechain": sidechain,
        "message": {
            "id": f"msg_{session}_{index}",
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": inp,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": write_5m,
                    "ephemeral_1h_input_tokens": 0,
                },
                "cache_read_input_tokens": read,
                "output_tokens": out,
            },
        },
    }


def user(session: str, minute: float, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": _stamp(minute),
        "message": {"role": "user", "content": text},
    }


def tool_result(session: str, minute: float, use_id: str, body: str) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": _stamp(minute),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": use_id, "content": body}],
        },
    }


def cold_context_session(rng: random.Random) -> list[dict]:
    """A session whose cache keeps being invalidated mid-run."""
    sid = "a3f21c88"
    rows = [user(sid, 2 * DAY, "Port the billing webhooks to the new queue consumer.")]
    rows.append(
        assistant(sid, 2 * DAY + 1, inp=62_000, write_5m=62_000, out=900, index=0)
    )
    for i in range(1, 11):
        # No cache reads at all: every turn re-sends the whole prefix.
        rows.append(
            assistant(
                sid,
                2 * DAY + 1 + i * 4,
                inp=rng.randint(58_000, 74_000),
                out=rng.randint(300, 1_100),
                index=i,
            )
        )
    return rows


def ballooned_session(rng: random.Random) -> list[dict]:
    """Context snowballs across a long session while output stays thin."""
    sid = "b7d40e12"
    rows = [user(sid, 5 * DAY, "Trace why the nightly reconciliation job is drifting.")]
    context = 96_000
    for i in range(26):
        context = min(context + rng.randint(6_000, 14_000), 420_000)
        rows.append(
            assistant(
                sid,
                5 * DAY + 2 + i * 6,
                inp=rng.randint(200, 900),
                read=context,
                out=rng.randint(90, 260),
                index=i,
            )
        )
    return rows


def redundant_tool_session(rng: random.Random) -> list[dict]:
    """The agent re-reads the same large file over and over."""
    sid = "c1e88a34"
    rows = [user(sid, 8 * DAY, "Add retry semantics to the payment client.")]
    blob = "".join(
        f"line {n}: payment client implementation detail\n" for n in range(700)
    )
    for i in range(6):
        use_id = f"tu_{i}"
        rows.append(
            assistant(
                sid,
                8 * DAY + 2 + i * 5,
                inp=rng.randint(400, 900),
                read=48_000,
                out=rng.randint(200, 500),
                tool={
                    "id": use_id,
                    "name": "Read",
                    "input": {"file_path": "/srv/payments/client.py"},
                },
                index=i,
            )
        )
        rows.append(tool_result(sid, 8 * DAY + 3 + i * 5, use_id, blob))
    return rows


def repeated_context_sessions() -> list[tuple[str, list[dict]]]:
    """The same house-rules block pasted into four separate sessions."""
    out = []
    for n, sid in enumerate(("d0a11f55", "d0a22f66", "d0a33f77", "d0a44f88")):
        base = (10 + n * 2) * DAY
        rows = [
            user(
                sid, base, f"Here is our setup.\n\n{HOUSE_RULES}\n\nAdd a health check."
            ),
            assistant(sid, base + 1, inp=9_000, write_5m=9_000, out=700, index=0),
            assistant(sid, base + 6, inp=300, read=9_300, out=500, index=1),
        ]
        out.append((sid, rows))
    return out


def subagent_session(rng: random.Random) -> list[dict]:
    """Most of the cost is sidechain turns re-deriving the parent's context."""
    sid = "e5c07b90"
    rows = [user(sid, 14 * DAY, "Audit every service for unbounded retries.")]
    rows.append(
        assistant(sid, 14 * DAY + 1, inp=24_000, write_5m=24_000, out=800, index=0)
    )
    for i in range(9):
        rows.append(
            assistant(
                sid,
                14 * DAY + 3 + i * 3,
                inp=rng.randint(52_000, 68_000),
                out=rng.randint(400, 900),
                sidechain=True,
                index=10 + i,
            )
        )
    rows.append(assistant(sid, 14 * DAY + 40, inp=900, read=25_000, out=1_400, index=30))
    return rows


def model_mismatch_session() -> list[dict]:
    """A premium model asked to do something very small."""
    sid = "f2b93d07"
    return [
        user(sid, 16 * DAY, "What is the default timeout on the http client?"),
        assistant(
            sid, 16 * DAY + 1, model=FABLE, inp=140_000, write_5m=8_000, out=180, index=0
        ),
    ]


def fat_prompt_session() -> list[dict]:
    """One enormous paste among otherwise ordinary prompts."""
    sid = "0c6ad4e1"
    dump = "\n".join(
        f"2026-06-14T02:{n % 60:02d}:11Z  worker-{n % 7}  WARN  "
        f"queue depth {900 + n} exceeded soft limit, deferring batch {n}"
        for n in range(1_400)
    )
    rows = [
        user(sid, 17 * DAY, "Why is the worker pool backing up?"),
        assistant(sid, 17 * DAY + 1, inp=3_000, write_5m=3_000, out=400, index=0),
        user(sid, 17 * DAY + 4, f"Here are the logs:\n\n{dump}"),
        assistant(sid, 17 * DAY + 5, inp=52_000, write_5m=52_000, out=900, index=1),
        user(sid, 17 * DAY + 10, "Which worker is worst?"),
        assistant(sid, 17 * DAY + 11, inp=400, read=55_000, out=600, index=2),
    ]
    return rows


def healthy_sessions(rng: random.Random) -> list[tuple[str, list[dict]]]:
    """Well-cached sessions, so the report is not uniformly bad news."""
    out = []
    for n, sid in enumerate(("11aa22bb", "33cc44dd", "55ee66ff")):
        base = (18 + n) * DAY
        rows = [user(sid, base, "Tidy up the config loader and add tests.")]
        rows.append(
            assistant(
                sid,
                base + 1,
                model=SONNET,
                inp=18_000,
                write_5m=18_000,
                out=1_200,
                index=0,
            )
        )
        ctx = 18_400
        for i in range(1, rng.randint(9, 16)):
            ctx += rng.randint(400, 1_800)
            rows.append(
                assistant(
                    sid,
                    base + 1 + i * 5,
                    model=SONNET,
                    inp=rng.randint(80, 400),
                    read=ctx,
                    out=rng.randint(500, 2_400),
                    index=i,
                )
            )
        out.append((sid, rows))
    return out


PROJECTS = {
    "a3f21c88": "-srv-acme-billing",
    "b7d40e12": "-srv-acme-ledger",
    "c1e88a34": "-srv-acme-payments",
    "d0a11f55": "-srv-acme-platform",
    "d0a22f66": "-srv-acme-platform",
    "d0a33f77": "-srv-acme-platform",
    "d0a44f88": "-srv-acme-platform",
    "e5c07b90": "-srv-acme-ledger",
    "f2b93d07": "-srv-acme-billing",
    "0c6ad4e1": "-srv-acme-workers",
    "11aa22bb": "-srv-acme-platform",
    "33cc44dd": "-srv-acme-workers",
    "55ee66ff": "-srv-acme-payments",
}


def build(root: Path) -> int:
    rng = random.Random(SEED)
    corpus: list[tuple[str, list[dict]]] = [
        ("a3f21c88", cold_context_session(rng)),
        ("b7d40e12", ballooned_session(rng)),
        ("c1e88a34", redundant_tool_session(rng)),
        ("e5c07b90", subagent_session(rng)),
        ("f2b93d07", model_mismatch_session()),
        ("0c6ad4e1", fat_prompt_session()),
    ]
    corpus.extend(repeated_context_sessions())
    corpus.extend(healthy_sessions(rng))

    written = 0
    for sid, rows in corpus:
        directory = root / PROJECTS[sid]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{sid}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="directory to write the corpus into")
    args = parser.parse_args()

    count = build(args.target)
    print(f"wrote {count} demo sessions to {args.target}")
    print(f"try:  proctor {args.target} --days 90")


if __name__ == "__main__":
    main()
