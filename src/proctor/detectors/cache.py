"""Cold-context detection: prompt prefixes paid for at full price twice."""

from __future__ import annotations

from collections.abc import Iterator

from ..types import Finding, Session
from .base import AuditContext, SessionDetector

COLD_TURN_THRESHOLD = 5_000
"""Uncached input below this is normal per-turn churn, not a lost cache."""

RECOVERY_FACTOR = 0.9
"""Fraction of the input/cache-read spread that better caching could recover.

Not 1.0: re-establishing the cache costs a write at 1.25x base input, so a
perfectly cached alternative is slightly more expensive than the pure read
price. The factor keeps the estimate on the conservative side.
"""


class ColdContextDetector(SessionDetector):
    kind = "cache_miss"
    label = "COLD CONTEXT"
    summary = "Large prompt prefixes reprocessed uncached mid-session."

    def inspect(self, session: Session, ctx: AuditContext) -> Iterator[Finding]:
        # The first turn of a session always pays full price — there is nothing
        # cached yet — so it is excluded.
        cold = [
            t
            for t in session.turns[1:]
            if t.cache_read == 0 and t.input_tokens > COLD_TURN_THRESHOLD
        ]
        if not cold:
            return

        tokens = sum(t.input_tokens for t in cold)
        price = ctx.prices.for_model(session.turns[0].model, session.turns[0].ts)
        spread = price.input - price.cache_read
        waste = tokens * spread / 1_000_000 * RECOVERY_FACTOR

        yield Finding(
            kind=self.kind,
            waste=waste,
            scope=session.short_id,
            project=session.project,
            detail=(
                f"{len(cold)} turn(s) reprocessed {tokens:,} uncached input tokens "
                f"mid-session. Cache reads cost 10% of base input, so this context "
                f"was paid for at full price a second time. Usually caused by "
                f"editing the system prompt, changing the tool set, or letting the "
                f"5-minute cache TTL lapse between turns."
            ),
            evidence={
                "cold_turns": len(cold),
                "uncached_tokens": tokens,
                "model": session.turns[0].model,
            },
        )
