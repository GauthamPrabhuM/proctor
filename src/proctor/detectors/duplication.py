"""Duplication checks: the same tokens paid for more than once.

Two flavours, one per class:

* :class:`RepeatedContextDetector` — the same large block pasted into several
  different sessions. That belongs in ``CLAUDE.md``, a skill, or a file the
  agent reads on demand.
* :class:`RedundantToolCallDetector` — the same tool call, with byte-identical
  arguments, issued repeatedly inside a single session. Each repeat pulls the
  same result back into context.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..transcripts import group_tool_calls
from ..types import Finding, Session
from .base import AuditContext, Detector, SessionDetector

MIN_REPEATS = 3
"""Two identical calls is often a legitimate re-check; three is a pattern."""

MIN_RESULT_TOKENS = 400
"""Ignore repeats of cheap calls — the waste is in the result, not the call."""


class RepeatedContextDetector(Detector):
    kind = "repeat"
    label = "REPEATED CONTEXT"
    summary = "The same large block pasted into multiple sessions."

    def scan(self, ctx: AuditContext) -> Iterator[Finding]:
        blocks = ctx.corpus.duplicated_blocks()
        blocks.sort(key=lambda b: -b.redundant_tokens)
        price = ctx.prices.fallback

        for block in blocks[: ctx.top]:
            waste = block.redundant_tokens * price.input / 1_000_000
            if not self.worth_reporting(waste):
                continue
            count = len(block.sessions)
            yield Finding(
                kind=self.kind,
                waste=waste,
                scope=f"{count} sessions",
                detail=(
                    f"The same ~{block.tokens:,}-token block appeared in {count} "
                    f"sessions (~{block.redundant_tokens:,} redundant tokens). Move "
                    f"it into CLAUDE.md, a skill, or a file the agent reads on "
                    f"demand instead of re-pasting it. “{block.preview}…”"
                ),
                evidence={
                    "block_tokens": block.tokens,
                    "sessions": count,
                    "redundant_tokens": block.redundant_tokens,
                },
            )


class RedundantToolCallDetector(SessionDetector):
    kind = "redundant_tool"
    label = "REDUNDANT TOOL CALLS"
    summary = "Identical tool calls repeated within one session."

    def inspect(self, session: Session, ctx: AuditContext) -> Iterator[Finding]:
        if not session.turns:
            return
        price = ctx.prices.for_model(session.turns[0].model, session.turns[0].ts)

        worst = None
        worst_waste = 0.0
        total_waste = 0.0
        offenders = 0

        for calls in group_tool_calls(session).values():
            if len(calls) < MIN_REPEATS:
                continue
            result_tokens = max(c.result_tokens for c in calls)
            if result_tokens < MIN_RESULT_TOKENS:
                continue
            # Each repeat past the first re-enters context as a fresh cache
            # write and is then carried by every later turn.
            redundant = result_tokens * (len(calls) - 1)
            waste = redundant * price.cache_write_5m / 1_000_000
            total_waste += waste
            offenders += 1
            if waste > worst_waste:
                worst_waste, worst = waste, (calls[0], len(calls), result_tokens)

        if worst is None:
            return

        call, repeats, result_tokens = worst
        others = (
            f" ({offenders - 1} other repeated call(s) in this session.)"
            if offenders > 1
            else ""
        )
        yield Finding(
            kind=self.kind,
            waste=total_waste,
            scope=session.short_id,
            project=session.project,
            detail=(
                f"{call.preview} ran {repeats} times with identical arguments, each "
                f"pulling back ~{result_tokens:,} tokens. The agent re-read work it "
                f"already had in context.{others}"
            ),
            evidence={
                "worst_call": call.preview,
                "repeats": repeats,
                "result_tokens": result_tokens,
                "repeated_calls": offenders,
            },
        )
