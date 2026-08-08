"""The detector interface.

A detector inspects the parsed corpus and emits :class:`~proctor.types.Finding`
objects with a dollar estimate attached. Detectors are independent — adding one
means adding a module here and registering it in ``__init__``; nothing else in
the codebase needs to change.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..pricing import PriceBook
from ..types import Finding, Session

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..transcripts import Corpus

__all__ = ["MIN_REPORTABLE_WASTE", "AuditContext", "Detector", "SessionDetector"]

MIN_REPORTABLE_WASTE = 0.01
"""Findings worth less than a cent are noise; they are dropped."""


@dataclass
class AuditContext:
    """Everything a detector is allowed to look at."""

    corpus: Corpus
    sessions: list[Session]
    prices: PriceBook
    top: int = 10
    """How many rows a corpus-wide detector should emit at most."""


class Detector:
    """Base class for a check. Subclasses override :meth:`scan`."""

    kind = "unknown"
    label = "UNKNOWN"
    summary = ""

    def scan(self, ctx: AuditContext) -> Iterable[Finding]:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def worth_reporting(waste: float) -> bool:
        return waste >= MIN_REPORTABLE_WASTE


class SessionDetector(Detector):
    """Convenience base for checks that look at one session at a time."""

    def scan(self, ctx: AuditContext) -> Iterator[Finding]:
        for session in ctx.sessions:
            for finding in self.inspect(session, ctx):
                if self.worth_reporting(finding.waste):
                    yield finding

    def inspect(
        self, session: Session, ctx: AuditContext
    ) -> Iterable[Finding]:  # pragma: no cover
        raise NotImplementedError
