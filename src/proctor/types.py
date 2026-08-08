"""Core data types shared across parsing, analysis, and rendering.

Everything downstream of :mod:`proctor.transcripts` operates on these objects
rather than raw dictionaries, so a field rename is a type error instead of a
silent ``KeyError`` at render time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

__all__ = [
    "Finding",
    "Report",
    "Session",
    "ToolCall",
    "Totals",
    "Turn",
    "UserPrompt",
]


@dataclass
class Turn:
    """One assistant API turn, with the token usage the API reported for it."""

    ts: datetime | None
    model: str
    input_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    output_tokens: int = 0
    sidechain: bool = False
    message_id: str | None = None

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def context_tokens(self) -> int:
        """Total prompt size for this turn, cached or not."""
        return self.input_tokens + self.cache_write + self.cache_read

    @property
    def billed_input(self) -> int:
        return self.context_tokens


@dataclass
class UserPrompt:
    """A prompt the human actually typed (not a tool result or meta message)."""

    tokens: int
    preview: str
    ts: datetime | None = None


@dataclass
class ToolCall:
    """A tool invocation and the size of the result it pulled into context."""

    name: str
    signature: str
    """Stable hash of the tool name plus its canonicalized input."""
    preview: str
    result_tokens: int = 0


@dataclass
class Session:
    """One Claude Code session: a transcript file's worth of turns."""

    id: str
    project: str
    path: Path
    turns: list[Turn] = field(default_factory=list)
    prompts: list[UserPrompt] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    cost: float = 0.0
    """Estimated spend in USD. Populated by :func:`proctor.analyze.audit`."""

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def n_turns(self) -> int:
        return len(self.turns)

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def cache_write(self) -> int:
        return sum(t.cache_write for t in self.turns)

    @property
    def cache_read(self) -> int:
        return sum(t.cache_read for t in self.turns)

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def peak_context(self) -> int:
        """Largest prompt the session ever sent — its high-water context mark."""
        return max((t.context_tokens for t in self.turns), default=0)

    @property
    def cache_hit_rate(self) -> float:
        denom = self.input_tokens + self.cache_write + self.cache_read
        return self.cache_read / denom if denom else 0.0

    @property
    def models(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for turn in self.turns:
            counts[turn.model] = counts.get(turn.model, 0) + 1
        return counts


@dataclass
class Finding:
    """A single piece of flagged waste, with a dollar estimate attached."""

    kind: str
    waste: float
    detail: str
    scope: str
    """What the finding applies to — a session id, or e.g. "4 sessions"."""
    project: str = "—"
    evidence: dict[str, object] = field(default_factory=dict)
    """Machine-readable backing numbers, surfaced in ``--json`` output."""


@dataclass
class Totals:
    cost: float = 0.0
    input_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output_tokens: int = 0
    turns: int = 0

    @property
    def cache_hit_rate(self) -> float:
        denom = self.input_tokens + self.cache_write + self.cache_read
        return self.cache_read / denom if denom else 0.0


@dataclass
class Report:
    """The finished audit: everything the renderers need and nothing more."""

    sessions: list[Session] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    daily: dict[str, float] = field(default_factory=dict)
    by_model: dict[str, float] = field(default_factory=dict)
    days: int = 0
    generated_at: datetime | None = None
    skipped_files: int = 0
    """Transcript files that could not be read at all."""

    @property
    def flagged_waste(self) -> float:
        return sum(f.waste for f in self.findings)

    @property
    def waste_share(self) -> float:
        return self.flagged_waste / self.totals.cost if self.totals.cost else 0.0
