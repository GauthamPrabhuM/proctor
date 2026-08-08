"""Human-readable terminal report."""

from __future__ import annotations

from typing import TextIO

from ..detectors import LABELS
from ..term import Style, human_money, sparkline, wrap
from ..types import Report

DETAIL_WIDTH = 76


def render(report: Report, stream: TextIO, style: Style, top: int = 10) -> None:
    out = stream.write
    totals = report.totals

    out("\n")
    out(
        style.heading(
            f"proctor — {len(report.sessions)} sessions, {totals.turns:,} API turns"
        )
    )
    if report.days:
        out(style.dim(f"  (last {report.days} days)"))
    out("\n\n")

    out(f"  estimated spend      {style.bold(human_money(totals.cost)):>22}\n")
    out(f"  input (uncached)     {totals.input_tokens:>14,} tok\n")
    out(f"  cache writes         {totals.cache_write:>14,} tok\n")
    out(
        f"  cache reads          {totals.cache_read:>14,} tok   "
        f"(hit rate {totals.cache_hit_rate:.0%})\n"
    )
    out(f"  output               {totals.output_tokens:>14,} tok\n")

    waste = human_money(report.flagged_waste)
    colour = style.red if report.waste_share > 0.25 else style.yellow
    out(
        f"  flagged waste        {colour(waste):>22}  ({report.waste_share:.0%} of spend)\n"
    )
    if report.skipped_files:
        out(style.dim(f"  ({report.skipped_files} transcript file(s) unreadable)\n"))
    out("\n")

    _daily(report, out, style)
    _by_model(report, out, style)
    _sessions(report, out, style, top)
    _findings(report, out, style, top)


def _daily(report: Report, out, style: Style) -> None:
    if len(report.daily) < 2:
        return
    days = sorted(report.daily.items())
    out(style.heading("daily burn") + "\n")
    out(f"  {sparkline([c for _, c in days])}\n")
    out(
        style.dim(
            f"  {days[0][0]} → {days[-1][0]}   peak "
            f"{human_money(max(c for _, c in days))}/day\n\n"
        )
    )


def _by_model(report: Report, out, style: Style) -> None:
    if not report.by_model:
        return
    out(style.heading("spend by model") + "\n")
    for model, cost in sorted(report.by_model.items(), key=lambda kv: -kv[1]):
        share = cost / report.totals.cost if report.totals.cost else 0.0
        out(f"  {model:<44} {human_money(cost):>10}  {share:>5.0%}\n")
    out("\n")


def _sessions(report: Report, out, style: Style, top: int) -> None:
    out(style.heading("top sessions by cost") + "\n")
    out(
        style.dim(
            f"  {'session':<10} {'project':<28} {'turns':>5} {'peak ctx':>10} "
            f"{'hit':>5} {'cost':>9}\n"
        )
    )
    for session in report.sessions[:top]:
        out(
            f"  {session.short_id:<10} {session.project[-28:]:<28} "
            f"{session.n_turns:>5} {session.peak_context:>10,} "
            f"{session.cache_hit_rate:>5.0%} {human_money(session.cost):>9}\n"
        )
    out("\n")


def _findings(report: Report, out, style: Style, top: int) -> None:
    out(style.heading("findings, ranked by estimated waste") + "\n")
    if not report.findings:
        out(style.green("  Nothing flagged. Clean bill of health.\n\n"))
        return
    for finding in report.findings[: top * 2]:
        label = LABELS.get(finding.kind, finding.kind.upper())
        out(
            f"\n  [{style.bold(human_money(finding.waste))}] {style.yellow(label)}  "
            + style.dim(f"({finding.scope}, {finding.project})")
            + "\n"
        )
        for line in wrap(finding.detail, DETAIL_WIDTH):
            out(f"      {line}\n")
    hidden = len(report.findings) - min(len(report.findings), top * 2)
    if hidden > 0:
        out(style.dim(f"\n  … {hidden} more finding(s); raise --top to see them.\n"))
    out("\n")
