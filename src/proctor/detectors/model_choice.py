"""Model mismatch: premium models doing work a cheaper tier would have handled."""

from __future__ import annotations

from collections.abc import Iterator

from ..pricing import Price
from ..types import Finding, Session
from .base import AuditContext, SessionDetector

PREMIUM_MARKERS = ("opus", "fable", "mythos")

MIN_SESSION_COST = 0.25
MAX_OUTPUT_TOKENS = 2_000
"""A premium model that produced very little output was probably overkill."""

MIN_SAVING = 0.05

CHEAPER_TIER = Price.from_input_output(3.0, 15.0)
"""Sonnet standard rates — the comparison baseline for the counterfactual."""

CHEAPER_TIER_NAME = "Sonnet"


class ModelMismatchDetector(SessionDetector):
    kind = "model_mismatch"
    label = "MODEL MISMATCH"
    summary = "Premium models on light work."

    def inspect(self, session: Session, ctx: AuditContext) -> Iterator[Finding]:
        premium = [
            t
            for t in session.turns
            if any(marker in (t.model or "").lower() for marker in PREMIUM_MARKERS)
        ]
        if not premium:
            return
        if session.cost <= MIN_SESSION_COST:
            return
        if session.output_tokens >= MAX_OUTPUT_TOKENS:
            return

        alternative = ctx.prices.hypothetical_cost(session.turns, CHEAPER_TIER)
        saving = session.cost - alternative
        if saving < MIN_SAVING:
            return

        yield Finding(
            kind=self.kind,
            waste=saving,
            scope=session.short_id,
            project=session.project,
            detail=(
                f"{premium[0].model} produced only {session.output_tokens:,} output "
                f"tokens for ${session.cost:.2f}. The same token volume on "
                f"{CHEAPER_TIER_NAME} would be ~${alternative:.2f}. Check whether the "
                f"task needed the top tier — model choice is yours to make, and this "
                f"is only a cost comparison, not a quality one."
            ),
            evidence={
                "model": premium[0].model,
                "output_tokens": session.output_tokens,
                "actual_cost": round(session.cost, 4),
                "alternative_cost": round(alternative, 4),
            },
        )
