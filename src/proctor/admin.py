"""Organization-level reporting via the Anthropic Admin API.

Local transcripts only cover the machine they are on. The Admin API covers the
whole organization, but reports aggregates rather than sessions — so this is a
complement to the transcript audit, not a replacement for it.

Requires an admin key (``sk-ant-admin-*``) in ``ANTHROPIC_ADMIN_KEY``. Admin
keys are org-wide credentials: proctor only ever issues GETs against the two
reporting endpoints below, and never writes.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TextIO

from .pricing import PriceBook
from .term import Style, human_money

API_ROOT = "https://api.anthropic.com"
USAGE_PATH = "/v1/organizations/usage_report/messages"
COST_PATH = "/v1/organizations/cost_report"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_ADMIN_KEY"
KEY_PREFIX = "sk-ant-admin"

MAX_PAGES = 100
"""Hard stop so a malformed pagination cursor cannot loop forever."""

LOW_HIT_RATE = 0.5
MIN_TOKENS_TO_ADVISE = 1_000_000
CACHE_RECOVERY = 0.7
"""Conservative share of the uncached input an org could realistically cache."""


class AdminError(Exception):
    """Any failure talking to the Admin API, with a human-readable message."""


@dataclass
class ModelUsage:
    model: str
    uncached_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_read + self.cache_write

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_read / self.total_input if self.total_input else 0.0


@dataclass
class OrgReport:
    days: int
    usage: list[ModelUsage] = field(default_factory=list)
    total_cost_usd: float = 0.0


def _get(path: str, key: str, params: dict[str, str]) -> list[dict]:
    """GET every page of a paginated Admin API endpoint."""
    records: list[dict] = []
    page: str | None = None

    for _ in range(MAX_PAGES):
        query = dict(params)
        if page:
            query["page"] = page
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(query, doseq=True)}"
        request = urllib.request.Request(
            url, headers={"x-api-key": key, "anthropic-version": API_VERSION}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise AdminError(f"{path} returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AdminError(f"could not reach {API_ROOT}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise AdminError(f"{path} returned a non-JSON response: {exc}") from exc

        records.extend(payload.get("data", []))
        if not payload.get("has_more") or not payload.get("next_page"):
            return records
        page = payload["next_page"]

    raise AdminError(f"{path}: stopped after {MAX_PAGES} pages of results")


def resolve_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get(KEY_ENV, "")
    if not key:
        raise AdminError(
            f"no admin key found. Set {KEY_ENV} to an {KEY_PREFIX}-* key "
            f"(Console → Settings → Admin keys)."
        )
    if not key.startswith(KEY_PREFIX):
        raise AdminError(
            f"{KEY_ENV} does not look like an admin key — it must start with "
            f"'{KEY_PREFIX}'. Regular API keys cannot read org reports."
        )
    return key


def fetch(days: int, key: str | None = None) -> OrgReport:
    """Pull usage and cost for the last ``days`` days."""
    key = resolve_key(key)
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )

    usage_buckets = _get(
        USAGE_PATH,
        key,
        {
            "starting_at": start,
            "bucket_width": "1d",
            "group_by[]": "model",
            "limit": "31",
        },
    )
    cost_buckets = _get(COST_PATH, key, {"starting_at": start, "limit": "31"})

    by_model: dict[str, ModelUsage] = {}
    for bucket in usage_buckets:
        for row in bucket.get("results", []):
            model = row.get("model") or "unknown"
            entry = by_model.setdefault(model, ModelUsage(model))
            entry.uncached_input += int(row.get("uncached_input_tokens") or 0)
            entry.cache_read += int(row.get("cache_read_input_tokens") or 0)
            creation = row.get("cache_creation")
            if isinstance(creation, dict):
                entry.cache_write += int(
                    creation.get("ephemeral_5m_input_tokens") or 0
                ) + int(creation.get("ephemeral_1h_input_tokens") or 0)
            else:
                entry.cache_write += int(row.get("cache_creation_input_tokens") or 0)
            entry.output += int(row.get("output_tokens") or 0)

    # The cost report reports amounts in minor units (cents).
    total_cents = sum(
        float(row.get("amount") or 0)
        for bucket in cost_buckets
        for row in bucket.get("results", [])
    )

    return OrgReport(
        days=days,
        usage=sorted(by_model.values(), key=lambda u: -u.total_input),
        total_cost_usd=total_cents / 100.0,
    )


def advisories(report: OrgReport, prices: PriceBook) -> list[str]:
    """Org-wide cache advice, one line per underperforming model."""
    notes: list[str] = []
    for entry in report.usage:
        if entry.total_input < MIN_TOKENS_TO_ADVISE:
            continue
        if entry.cache_hit_rate >= LOW_HIT_RATE:
            continue
        price = prices.for_model(entry.model)
        saving = (
            entry.uncached_input
            * (price.input - price.cache_read)
            / 1_000_000
            * CACHE_RECOVERY
        )
        if saving < 0.01:
            continue
        notes.append(
            f"{entry.model}: cache hit rate {entry.cache_hit_rate:.0%} — better "
            f"caching could save roughly {human_money(saving)} over this period."
        )
    return notes


def render(
    report: OrgReport,
    stream: TextIO,
    style: Style,
    prices: PriceBook | None = None,
) -> None:
    prices = prices or PriceBook()
    out = stream.write

    out("\n" + style.heading(f"org usage — last {report.days} days (Admin API)") + "\n\n")
    header = (
        f"  {'model':<40} {'uncached in':>13} {'cache read':>13} "
        f"{'cache write':>13} {'output':>11} {'hit':>6}\n"
    )
    out(style.dim(header))
    for entry in report.usage:
        out(
            f"  {entry.model:<40} {entry.uncached_input:>13,} {entry.cache_read:>13,} "
            f"{entry.cache_write:>13,} {entry.output:>11,} "
            f"{entry.cache_hit_rate:>6.0%}\n"
        )

    out(
        f"\n  billed org cost over period: {style.bold(human_money(report.total_cost_usd))}\n"
    )
    notes = advisories(report, prices)
    if notes:
        out("\n")
        for note in notes:
            out(style.yellow(f"  ! {note}") + "\n")
    out("\n")


def to_dict(report: OrgReport, prices: PriceBook | None = None) -> dict[str, Any]:
    prices = prices or PriceBook()
    return {
        "window_days": report.days,
        "billed_cost_usd": round(report.total_cost_usd, 4),
        "by_model": [
            {
                "model": e.model,
                "uncached_input_tokens": e.uncached_input,
                "cache_read_tokens": e.cache_read,
                "cache_write_tokens": e.cache_write,
                "output_tokens": e.output,
                "cache_hit_rate": round(e.cache_hit_rate, 4),
            }
            for e in report.usage
        ],
        "advisories": advisories(report, prices),
    }


def known_endpoints() -> Sequence[str]:
    return (USAGE_PATH, COST_PATH)
