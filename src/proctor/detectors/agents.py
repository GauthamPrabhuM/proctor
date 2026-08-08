"""Subagent burn: sidechains that re-derive context the parent already held."""

from __future__ import annotations

from collections.abc import Iterator

from ..types import Finding, Session
from .base import AuditContext, SessionDetector

MIN_SESSION_COST = 0.50
"""Don't flag subagent use on sessions that were cheap regardless."""

DOMINANCE = 0.4
"""Share of session cost above which subagents are the main line item."""

RECOVERABLE = 0.5
"""Assumed fraction of subagent spend that inline work would have avoided.

A subagent that re-reads the repo duplicates context the parent had; one doing
genuinely independent work does not. Half is the midpoint between those cases.
"""


class SubagentBurnDetector(SessionDetector):
    kind = "sidechain"
    label = "SUBAGENT BURN"
    summary = "Subagents consuming most of a session's cost."

    def inspect(self, session: Session, ctx: AuditContext) -> Iterator[Finding]:
        if session.cost <= MIN_SESSION_COST:
            return
        sidechain_cost = sum(
            ctx.prices.turn_cost(t) for t in session.turns if t.sidechain
        )
        share = sidechain_cost / session.cost
        if share <= DOMINANCE:
            return

        yield Finding(
            kind=self.kind,
            waste=sidechain_cost * RECOVERABLE,
            scope=session.short_id,
            project=session.project,
            detail=(
                f"Subagents consumed ${sidechain_cost:.2f} of this session's "
                f"${session.cost:.2f} ({share:.0%}). Each subagent starts cold and "
                f"re-derives context the parent already had. Worth it for genuinely "
                f"parallel work; expensive for anything sequential."
            ),
            evidence={
                "sidechain_cost": round(sidechain_cost, 4),
                "session_cost": round(session.cost, 4),
                "share": round(share, 4),
            },
        )
