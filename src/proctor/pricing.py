"""Model pricing, in US dollars per million tokens.

Rules are matched by substring against the model id, first match wins, so more
specific patterns must come first. A rule may carry ``until``, which limits it
to turns that happened before that instant — that is how introductory pricing
is handled without special-casing it in the cost calculation.

Rates are list prices as published on platform.claude.com/docs/en/pricing and
are current as of August 2026. To override them without editing this file, see
``docs/configuration.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["DEFAULT_RULES", "TOKENS_PER_MILLION", "Price", "PriceBook", "PriceRule"]

TOKENS_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens for each way a token can be billed."""

    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float

    @classmethod
    def from_input_output(cls, inp: float, out: float) -> Price:
        """Build a price from the two headline rates.

        Cache multipliers are uniform across the model line: a 5-minute cache
        write costs 1.25x base input, a 1-hour write 2x, and a cache read 0.1x.
        """
        return cls(
            input=inp,
            cache_write_5m=round(inp * 1.25, 6),
            cache_write_1h=round(inp * 2.0, 6),
            cache_read=round(inp * 0.1, 6),
            output=out,
        )

    def as_dict(self) -> dict:
        return {
            "input": self.input,
            "cache_write_5m": self.cache_write_5m,
            "cache_write_1h": self.cache_write_1h,
            "cache_read": self.cache_read,
            "output": self.output,
        }


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC so comparisons never raise."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PriceRule:
    match: str
    """Lowercase substring tested against the model id."""
    price: Price
    until: datetime | None = None
    """If set, the rule only applies to turns strictly before this instant."""
    note: str = ""

    def applies(self, model: str, ts: datetime | None) -> bool:
        if self.match not in model:
            return False
        if self.until is None:
            return True
        moment = ts or datetime.now(timezone.utc)
        return _as_utc(moment) < _as_utc(self.until)


_SONNET_5_INTRO_ENDS = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Order matters: the first matching rule wins.
DEFAULT_RULES: Sequence[PriceRule] = (
    # Fable / Mythos tier.
    PriceRule("fable", Price.from_input_output(10.0, 50.0)),
    PriceRule("mythos", Price.from_input_output(10.0, 50.0)),
    # Opus 4.0 / 4.1 and Claude 3 Opus predate the Opus-tier price cut and must
    # be matched before the generic "opus" rule below.
    PriceRule("opus-4-1", Price.from_input_output(15.0, 75.0)),
    PriceRule("opus-4-2025", Price.from_input_output(15.0, 75.0)),
    PriceRule("3-opus", Price.from_input_output(15.0, 75.0)),
    # Opus 4.5 through Opus 5.
    PriceRule("opus", Price.from_input_output(5.0, 25.0)),
    # Sonnet 5 launched with introductory pricing that ends 2026-09-01.
    PriceRule(
        "sonnet-5",
        Price.from_input_output(2.0, 10.0),
        until=_SONNET_5_INTRO_ENDS,
        note="Sonnet 5 introductory pricing",
    ),
    PriceRule("sonnet", Price.from_input_output(3.0, 15.0)),
    # Claude 3 Haiku ids read "claude-3-5-haiku-*", not "haiku-3-5-*", so the
    # version digits come before the family name in the pattern.
    PriceRule("3-5-haiku", Price.from_input_output(0.8, 4.0)),
    PriceRule("3-haiku", Price.from_input_output(0.25, 1.25)),
    PriceRule("haiku", Price.from_input_output(1.0, 5.0)),
)

DEFAULT_PRICE = Price.from_input_output(3.0, 15.0)
"""Fallback for an unrecognised model id — assumes Sonnet-tier standard rates."""


class PriceBook:
    """Resolves a model id (and timestamp) to a :class:`Price`."""

    def __init__(
        self,
        rules: Iterable[PriceRule] | None = None,
        fallback: Price = DEFAULT_PRICE,
    ) -> None:
        self.rules: list[PriceRule] = list(DEFAULT_RULES if rules is None else rules)
        self.fallback = fallback

    def for_model(self, model: str, ts: datetime | None = None) -> Price:
        key = (model or "").lower()
        for rule in self.rules:
            if rule.applies(key, ts):
                return rule.price
        return self.fallback

    def turn_cost(self, turn) -> float:
        """Cost in USD of one :class:`~proctor.types.Turn`."""
        p = self.for_model(turn.model, turn.ts)
        cents = (
            turn.input_tokens * p.input
            + turn.cache_write_5m * p.cache_write_5m
            + turn.cache_write_1h * p.cache_write_1h
            + turn.cache_read * p.cache_read
            + turn.output_tokens * p.output
        )
        return cents / TOKENS_PER_MILLION

    def hypothetical_cost(self, turns: Iterable, price: Price) -> float:
        """Cost of ``turns`` had every one of them run on ``price``.

        Used by the model-mismatch detector to price the counterfactual.
        """
        total = 0.0
        for turn in turns:
            total += (
                turn.input_tokens * price.input
                + turn.cache_write_5m * price.cache_write_5m
                + turn.cache_write_1h * price.cache_write_1h
                + turn.cache_read * price.cache_read
                + turn.output_tokens * price.output
            ) / TOKENS_PER_MILLION
        return total

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Mapping[str, float]]) -> PriceBook:
        """Build a price book from user config.

        ``overrides`` maps a match pattern to either ``{"input": x, "output": y}``
        (cache rates derived) or an explicit five-field price. Overrides are
        matched before the built-in rules, so a pattern present in both wins.
        """
        extra: list[PriceRule] = []
        for pattern, spec in overrides.items():
            if "cache_read" in spec:
                price = Price(
                    input=float(spec["input"]),
                    cache_write_5m=float(spec["cache_write_5m"]),
                    cache_write_1h=float(spec["cache_write_1h"]),
                    cache_read=float(spec["cache_read"]),
                    output=float(spec["output"]),
                )
            else:
                price = Price.from_input_output(
                    float(spec["input"]), float(spec["output"])
                )
            extra.append(PriceRule(pattern.lower(), price))
        return cls(rules=extra + list(DEFAULT_RULES))
