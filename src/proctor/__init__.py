"""proctor — audit Claude Code and Anthropic API usage for token waste.

Typical library use::

    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from proctor import audit, load

    since = datetime.now(timezone.utc) - timedelta(days=7)
    corpus = load([Path("~/.claude/projects").expanduser()], since)
    report = audit(corpus, days=7)
    print(report.flagged_waste)
"""

from __future__ import annotations

__version__ = "0.2.0"

from .analyze import audit
from .pricing import Price, PriceBook
from .transcripts import Corpus, load
from .types import Finding, Report, Session, Turn

__all__ = [
    "Corpus",
    "Finding",
    "Price",
    "PriceBook",
    "Report",
    "Session",
    "Turn",
    "__version__",
    "audit",
    "load",
]
