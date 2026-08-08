"""Self-contained HTML dashboard.

No templating dependency and no external assets: the output is a single file
that opens offline, which matters because it contains excerpts of your own
prompts and should not be fetching anything from the network.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..detectors import LABELS
from ..types import Report

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #fafaf7; --panel: #fff; --ink: #1a1a1a; --muted: #6b6b6b;
  --line: #e2e0da; --accent: #c96442;
  --t-cache_miss: #fde5d8; --t-balloon: #fdeecd; --t-repeat: #e3ecfb;
  --t-fat_prompt: #eee6fb; --t-sidechain: #e0f3e6; --t-model_mismatch: #fbe3e8;
  --t-redundant_tool: #e6eeed;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161614; --panel: #1f1f1d; --ink: #ececea; --muted: #9a9a95;
    --line: #33332f; --accent: #d97f5c;
    --t-cache_miss: #4a3128; --t-balloon: #4a3f22; --t-repeat: #24354f;
    --t-fat_prompt: #362a4d; --t-sidechain: #22402c; --t-model_mismatch: #4a2530;
    --t-redundant_tool: #26332f;
  }
}
* { box-sizing: border-box; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0 auto; max-width: 1000px; padding: 2rem 1rem 4rem;
  color: var(--ink); background: var(--bg); }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2.25rem 0 .75rem; letter-spacing: .01em; }
.sub { color: var(--muted); font-size: .85rem; margin: 0 0 1.5rem; }
.cards { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: .9rem 1.1rem; }
.card b { display: block; font-size: 1.6rem; font-variant-numeric: tabular-nums; }
.card span { color: var(--muted); font-size: .78rem; }
.chart { display: flex; align-items: flex-end; gap: 3px; height: 150px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 1rem; }
.bar { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
  align-items: center; height: 100%; min-width: 6px; }
.bar .fill { width: 100%; background: var(--accent); border-radius: 3px 3px 0 0; }
.bar span { font-size: .58rem; color: var(--muted); margin-top: 5px;
  writing-mode: vertical-rl; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
td, th { padding: .5rem .75rem; border-bottom: 1px solid var(--line); text-align: left;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
th { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
tr:last-child td { border-bottom: none; }
.find { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: .85rem 1.1rem; margin: .6rem 0; }
.find b { font-variant-numeric: tabular-nums; }
.tag { display: inline-block; font-size: .65rem; font-weight: 700; letter-spacing: .06em;
  padding: .18rem .55rem; border-radius: 99px; margin-right: .6rem; color: #1a1a1a;
  background: var(--line); }
@media (prefers-color-scheme: dark) { .tag { color: var(--ink); } }
.meta { color: var(--muted); font-size: .8rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .8rem; }
"""

_TAGS = "".join(f".tag.{kind} {{ background: var(--t-{kind}); }}\n" for kind in LABELS)


def escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _bars(report: Report) -> str:
    days = sorted(report.daily.items())
    if not days:
        return "<p class='meta'>No dated turns in this window.</p>"
    peak = max(cost for _, cost in days) or 1.0
    return "".join(
        f'<div class="bar" title="{escape(day)}: ${cost:,.2f}">'
        f'<div class="fill" style="height:{max(2.0, cost / peak * 100):.0f}%"></div>'
        f"<span>{escape(day[5:])}</span></div>"
        for day, cost in days
    )


def _rows(cells: Iterable[Iterable[object]]) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in cells
    )


def build(report: Report, top: int = 10) -> str:
    totals = report.totals
    generated = report.generated_at or datetime.now()

    sessions = _rows(
        (
            s.short_id,
            s.project[-40:],
            s.n_turns,
            f"{s.peak_context:,}",
            f"{s.cache_hit_rate:.0%}",
            f"${s.cost:,.2f}",
        )
        for s in report.sessions[:top]
    )
    models = _rows(
        (model, f"${cost:,.2f}")
        for model, cost in sorted(report.by_model.items(), key=lambda kv: -kv[1])
    )
    findings = (
        "".join(
            f'<div class="find"><span class="tag {escape(f.kind)}">'
            f"{escape(LABELS.get(f.kind, f.kind))}</span>"
            f"<b>${f.waste:,.2f}</b> — {escape(f.detail)}<br>"
            f'<span class="meta">{escape(f.scope)} · {escape(f.project)}</span></div>'
            for f in report.findings[: top * 2]
        )
        or "<p class='meta'>Nothing flagged.</p>"
    )

    window = f"last {report.days} days" if report.days else "all available history"

    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>proctor — usage audit</title>
<style>{_CSS}{_TAGS}</style>
<h1>proctor — usage audit</h1>
<p class="sub">{escape(window)} · {len(report.sessions)} sessions ·
{totals.turns:,} API turns</p>
<div class="cards">
  <div class="card"><b>${totals.cost:,.2f}</b><span>estimated spend</span></div>
  <div class="card"><b>{totals.cache_hit_rate:.0%}</b><span>cache hit rate</span></div>
  <div class="card"><b>${report.flagged_waste:,.2f}</b><span>flagged waste
    ({report.waste_share:.0%})</span></div>
  <div class="card"><b>{totals.output_tokens:,}</b><span>output tokens</span></div>
</div>
<h2>Daily burn</h2>
<div class="scroll"><div class="chart">{_bars(report)}</div></div>
<h2>Findings</h2>
{findings}
<h2>Top sessions by cost</h2>
<div class="scroll"><table>
<tr><th>session</th><th>project</th><th>turns</th><th>peak context</th>
<th>cache hit</th><th>cost</th></tr>
{sessions}</table></div>
<h2>Spend by model</h2>
<div class="scroll"><table>
<tr><th>model</th><th>cost</th></tr>
{models}</table></div>
<footer>
Costs are estimated from per-turn usage at list prices and will not match an
invoice exactly. Generated {generated:%Y-%m-%d %H:%M} UTC by
<a href="https://github.com/GauthamPrabhuM/proctor">proctor</a>.
This file contains excerpts of your own prompts — treat it as private.
</footer>"""


def render(report: Report, path: Path, top: int = 10) -> None:
    Path(path).write_text(build(report, top), encoding="utf-8")
