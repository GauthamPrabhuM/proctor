"""Machine-readable output.

The shape is a stable contract — see ``docs/json-schema.md``. Fields are added
in minor releases; existing fields are only removed in a major one.
"""

from __future__ import annotations

import json
from typing import Any, TextIO

from ..types import Report

SCHEMA_VERSION = 1


def to_dict(report: Report, top: int = 10) -> dict[str, Any]:
    totals = report.totals
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "window_days": report.days,
        "totals": {
            "estimated_cost_usd": round(totals.cost, 6),
            "input_tokens": totals.input_tokens,
            "cache_write_tokens": totals.cache_write,
            "cache_read_tokens": totals.cache_read,
            "output_tokens": totals.output_tokens,
            "turns": totals.turns,
            "cache_hit_rate": round(totals.cache_hit_rate, 4),
        },
        "flagged_waste_usd": round(report.flagged_waste, 6),
        "flagged_waste_share": round(report.waste_share, 4),
        "by_model": {m: round(c, 6) for m, c in report.by_model.items()},
        "daily": {d: round(c, 6) for d, c in sorted(report.daily.items())},
        "sessions": [
            {
                "id": s.id,
                "project": s.project,
                "turns": s.n_turns,
                "estimated_cost_usd": round(s.cost, 6),
                "peak_context_tokens": s.peak_context,
                "cache_hit_rate": round(s.cache_hit_rate, 4),
                "input_tokens": s.input_tokens,
                "cache_write_tokens": s.cache_write,
                "cache_read_tokens": s.cache_read,
                "output_tokens": s.output_tokens,
                "started_at": s.first_ts.isoformat() if s.first_ts else None,
                "ended_at": s.last_ts.isoformat() if s.last_ts else None,
            }
            for s in report.sessions[:top]
        ],
        "findings": [
            {
                "kind": f.kind,
                "estimated_waste_usd": round(f.waste, 6),
                "scope": f.scope,
                "project": f.project,
                "detail": f.detail,
                "evidence": f.evidence,
            }
            for f in report.findings
        ],
    }


def render(report: Report, stream: TextIO, top: int = 10) -> None:
    json.dump(to_dict(report, top), stream, indent=2, default=str)
    stream.write("\n")
