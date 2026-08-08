"""Detector registry.

To add a check: write a :class:`~proctor.detectors.base.Detector` subclass in a
module here and add it to :data:`ALL_DETECTORS`. The CLI's ``--only`` and
``--skip`` flags, the JSON schema, and the HTML legend all derive from this
list, so nothing else needs updating.
"""

from __future__ import annotations

from collections.abc import Sequence

from .agents import SubagentBurnDetector
from .base import AuditContext, Detector, SessionDetector
from .cache import ColdContextDetector
from .context import BalloonDetector
from .duplication import RedundantToolCallDetector, RepeatedContextDetector
from .model_choice import ModelMismatchDetector
from .prompts import OversizedPromptDetector

ALL_DETECTORS: Sequence[Detector] = (
    ColdContextDetector(),
    BalloonDetector(),
    RepeatedContextDetector(),
    RedundantToolCallDetector(),
    OversizedPromptDetector(),
    SubagentBurnDetector(),
    ModelMismatchDetector(),
)

KINDS: list[str] = [d.kind for d in ALL_DETECTORS]

LABELS: dict[str, str] = {d.kind: d.label for d in ALL_DETECTORS}

SUMMARIES: dict[str, str] = {d.kind: d.summary for d in ALL_DETECTORS}


def select(only: Sequence[str] = (), skip: Sequence[str] = ()) -> list[Detector]:
    """Filter the registry by finding kind.

    ``only`` wins when both are supplied for the same kind.
    """
    chosen = list(ALL_DETECTORS)
    if only:
        wanted = set(only)
        chosen = [d for d in chosen if d.kind in wanted]
    if skip:
        unwanted = set(skip)
        chosen = [d for d in chosen if d.kind not in unwanted]
    return chosen


__all__ = [
    "ALL_DETECTORS",
    "KINDS",
    "LABELS",
    "SUMMARIES",
    "AuditContext",
    "Detector",
    "SessionDetector",
    "select",
]
