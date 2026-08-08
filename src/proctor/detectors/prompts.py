"""Oversized prompts: text that rides along in context for the rest of a session."""

from __future__ import annotations

from collections.abc import Iterator

from ..types import Finding, Session, UserPrompt
from .base import AuditContext, Detector

MIN_SAMPLE = 5
"""Below this many prompts there is no meaningful median to compare against."""

MEDIAN_MULTIPLE = 6
FLOOR_TOKENS = 3_000
"""A prompt is only flagged if it clears both the relative and absolute bars."""


class OversizedPromptDetector(Detector):
    kind = "fat_prompt"
    label = "OVERSIZED PROMPT"
    summary = "Prompts far larger than your median, carried by every later turn."

    def scan(self, ctx: AuditContext) -> Iterator[Finding]:
        prompts: list[tuple[UserPrompt, Session]] = [
            (p, s) for s in ctx.sessions for p in s.prompts
        ]
        if len(prompts) < MIN_SAMPLE:
            return

        sizes = sorted(p.tokens for p, _ in prompts)
        median = sizes[len(sizes) // 2]
        threshold = max(median * MEDIAN_MULTIPLE, FLOOR_TOKENS)

        oversized = sorted(
            (pair for pair in prompts if pair[0].tokens > threshold),
            key=lambda pair: -pair[0].tokens,
        )

        for prompt, session in oversized[: ctx.top]:
            price = ctx.prices.for_model(
                session.turns[0].model if session.turns else "",
                session.turns[0].ts if session.turns else None,
            )
            waste = prompt.tokens * price.input / 1_000_000
            if not self.worth_reporting(waste):
                continue
            yield Finding(
                kind=self.kind,
                waste=waste,
                scope=session.short_id,
                project=session.project,
                detail=(
                    f"~{prompt.tokens:,}-token prompt against a median of ~{median:,}. "
                    f"That text stays in context for every turn that follows it. "
                    f"“{prompt.preview}…”"
                ),
                evidence={
                    "prompt_tokens": prompt.tokens,
                    "median_tokens": median,
                    "threshold_tokens": threshold,
                },
            )
