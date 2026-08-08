"""Ballooned sessions: context that snowballs while output stays flat."""

from __future__ import annotations

from collections.abc import Iterator

from ..types import Finding, Session
from .base import AuditContext, SessionDetector

CONTEXT_THRESHOLD = 120_000
"""Peak prompt size at which carrying context starts to dominate the bill."""

OUTPUT_RATIO = 0.02
"""Below this output-to-context ratio the session is mostly re-reading itself."""

MIN_TURNS = 10

BLOAT_SHARE = 0.6
"""Share of the back half's context cost attributed to avoidable accumulation.

The back half of a ballooned session still does real work, so only part of what
it spends re-reading context is recoverable. 0.6 is a judgement call, held
constant so findings are comparable between runs.
"""


class BalloonDetector(SessionDetector):
    kind = "balloon"
    label = "BALLOONED SESSION"
    summary = "Context grew large while output stayed small."

    def inspect(self, session: Session, ctx: AuditContext) -> Iterator[Finding]:
        peak = session.peak_context
        output = session.output_tokens
        if peak < CONTEXT_THRESHOLD:
            return
        if session.n_turns < MIN_TURNS:
            return
        if output >= peak * OUTPUT_RATIO:
            return

        tail = session.turns[session.n_turns // 2 :]
        tail_context_cost = 0.0
        for turn in tail:
            price = ctx.prices.for_model(turn.model, turn.ts)
            tail_context_cost += (
                turn.input_tokens * price.input
                + turn.cache_read * price.cache_read
                + turn.cache_write_5m * price.cache_write_5m
                + turn.cache_write_1h * price.cache_write_1h
            ) / 1_000_000

        yield Finding(
            kind=self.kind,
            waste=tail_context_cost * BLOAT_SHARE,
            scope=session.short_id,
            project=session.project,
            detail=(
                f"Context grew to {peak:,} tokens over {session.n_turns} turns while "
                f"producing only {output:,} output tokens ({output / peak:.1%}). The "
                f"back half of the session spent ~${tail_context_cost:.2f} re-reading "
                f"accumulated context. Compact or restart before a session snowballs."
            ),
            evidence={
                "peak_context_tokens": peak,
                "output_tokens": output,
                "turns": session.n_turns,
                "tail_context_cost": round(tail_context_cost, 4),
            },
        )
