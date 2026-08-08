"""Turns a parsed corpus into a finished :class:`~proctor.types.Report`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from .detectors import ALL_DETECTORS, AuditContext, Detector
from .pricing import PriceBook
from .transcripts import Corpus
from .types import Report, Session, Totals

__all__ = ["audit", "price_sessions"]


def price_sessions(sessions: Sequence[Session], prices: PriceBook) -> None:
    """Populate ``Session.cost`` in place. Detectors rely on this having run."""
    for session in sessions:
        session.cost = sum(prices.turn_cost(t) for t in session.turns)


def audit(
    corpus: Corpus,
    prices: PriceBook | None = None,
    detectors: Sequence[Detector] | None = None,
    top: int = 10,
    days: int = 0,
) -> Report:
    """Cost every session, run the detectors, and assemble the report."""
    prices = prices or PriceBook()
    detectors = ALL_DETECTORS if detectors is None else detectors

    sessions = corpus.with_turns()
    price_sessions(sessions, prices)

    report = Report(
        days=days,
        generated_at=datetime.now(timezone.utc),
        skipped_files=corpus.files_skipped,
    )
    totals = Totals()

    for session in sessions:
        totals.cost += session.cost
        totals.input_tokens += session.input_tokens
        totals.cache_write += session.cache_write
        totals.cache_read += session.cache_read
        totals.output_tokens += session.output_tokens
        totals.turns += session.n_turns

        for turn in session.turns:
            cost = prices.turn_cost(turn)
            report.by_model[turn.model] = report.by_model.get(turn.model, 0.0) + cost
            if turn.ts is not None:
                day = turn.ts.date().isoformat()
                report.daily[day] = report.daily.get(day, 0.0) + cost

    report.totals = totals

    ctx = AuditContext(corpus=corpus, sessions=sessions, prices=prices, top=top)
    for detector in detectors:
        report.findings.extend(detector.scan(ctx))

    report.findings.sort(key=lambda f: -f.waste)
    report.sessions = sorted(sessions, key=lambda s: -s.cost)
    return report
