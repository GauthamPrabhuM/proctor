"""Small terminal helpers: colour, wrapping, and sparklines.

Colour is opt-out via ``NO_COLOR`` (see https://no-color.org) or ``--no-color``,
and is disabled automatically when stdout is not a TTY so piped output stays
clean.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

__all__ = ["Style", "human_money", "sparkline", "wrap"]

_SPARKS = "▁▂▃▄▅▆▇█"


class Style:
    """ANSI styling that degrades to plain text when colour is unavailable."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    @classmethod
    def detect(cls, force_off: bool = False) -> Style:
        if force_off or os.environ.get("NO_COLOR") is not None:
            return cls(False)
        return cls(sys.stdout.isatty())

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def heading(self, text: str) -> str:
        return self.bold(self.cyan(text))


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap that also breaks words longer than ``width``."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def sparkline(values: Sequence[float]) -> str:
    """Render a sequence as a single line of block characters."""
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return _SPARKS[0] * len(values)
    scale = len(_SPARKS) - 1
    return "".join(_SPARKS[round(v / peak * scale)] for v in values)


def human_money(amount: float) -> str:
    return f"${amount:,.2f}"
