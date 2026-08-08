"""Self-contained HTML dashboard.

No templating dependency and no external assets: the output is a single file
that opens offline, which matters because it contains excerpts of your own
prompts and should not be fetching anything from the network. That constraint
also rules out JavaScript, so every affordance here is CSS or a native ``title``
tooltip.

Colour follows the job each mark does rather than the brand:

* **Data marks** (daily cost, finding impact, model share) are one blue, because
  every one of them encodes magnitude for a single series.
* **Status** (cache hit rate) uses the reserved good / warning / critical steps,
  always beside the numeric value so state is never carried by colour alone.
* **Brand teal** is confined to chrome — headings, rules, links. It is
  deliberately *not* a data mark: measured against the status green it sits at a
  normal-vision ΔE of 10, well under the 15 floor, so a teal bar would read as a
  "good" badge.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from ..detectors import LABELS
from ..types import Report, Session

HIT_RATE_GOOD = 0.85
HIT_RATE_WARN = 0.60
"""Cache hit rate thresholds for the status colour on the sessions table."""

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f8f8;
  --panel: #ffffff;
  --sunken: #f1f4f3;
  --ink: #10201c;
  --muted: #5a6b66;
  --faint: #83948f;
  --line: #e3e8e6;
  --line-soft: #eef1f0;
  --brand: #16584a;
  --series: #2a78d6;
  --series-soft: #dbe8f9;
  --good: #0ca30c;
  --warn: #fab219;
  --crit: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1513;
    --panel: #151d1a;
    --sunken: #111917;
    --ink: #e5ebe8;
    --muted: #94a39e;
    --faint: #6f7e79;
    --line: #253029;
    --line-soft: #1c2521;
    --brand: #5fbfa3;
    --series: #3987e5;
    --series-soft: #1b2c3f;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1513;
  --panel: #151d1a;
  --sunken: #111917;
  --ink: #e5ebe8;
  --muted: #94a39e;
  --faint: #6f7e79;
  --line: #253029;
  --line-soft: #1c2521;
  --brand: #5fbfa3;
  --series: #3987e5;
  --series-soft: #1b2c3f;
}

* { box-sizing: border-box; }

body {
  margin: 0 auto;
  max-width: 1040px;
  padding: 2.5rem 1.25rem 5rem;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}

.mono, .num {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

/* ---- masthead ---- */
header { border-bottom: 1px solid var(--line); padding-bottom: 1.5rem; }
h1 {
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
h1 .tick { color: var(--brand); }
.sub { color: var(--muted); font-size: 0.85rem; margin: 0.3rem 0 0; }

h2 {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.11em;
  color: var(--faint);
  font-weight: 600;
  margin: 2.75rem 0 0.9rem;
}

/* ---- headline + supporting tiles ---- */
.headline {
  display: grid;
  gap: 1rem;
  grid-template-columns: 1fr;
  margin-top: 1.75rem;
}
@media (min-width: 760px) { .headline { grid-template-columns: 1.25fr 2fr; } }

.hero, .tile {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 1.15rem 1.3rem;
}
.hero .figure {
  display: block;
  font-size: 2.5rem;
  line-height: 1.1;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.hero .label { display: block; color: var(--muted); font-size: 0.82rem; margin-top: 0.3rem; }
.hero .of { color: var(--faint); font-size: 0.78rem; margin-top: 0.55rem; display: block; }

.tiles { display: grid; gap: 1rem; grid-template-columns: repeat(3, 1fr); }
@media (max-width: 560px) { .tiles { grid-template-columns: 1fr; } }
.tile .figure { display: block; font-size: 1.45rem; font-weight: 600; letter-spacing: -0.015em; }
.tile .label { display: block; color: var(--muted); font-size: 0.76rem; margin-top: 0.2rem; }

/* ---- daily burn chart ---- */
.chart-frame {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 1.1rem 1.25rem 0.9rem;
}
.chart-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 0.75rem;
  color: var(--faint);
  margin-bottom: 0.55rem;
}
.plot {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 132px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 1px;
}
.col { flex: 1 1 0; min-width: 5px; height: 100%; display: flex; align-items: flex-end; }
.col .fill {
  width: 100%;
  background: var(--series);
  border-radius: 3px 3px 0 0;
  min-height: 2px;
}
.col.peak .fill { background: var(--series); outline: 2px solid var(--series-soft); }
.axis {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--faint);
  margin-top: 0.4rem;
}

/* ---- findings ---- */
.finding {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 0.95rem 1.2rem 1.05rem;
  margin-bottom: 0.7rem;
}
.finding-top {
  display: flex;
  align-items: baseline;
  gap: 0.7rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}
.amount { font-size: 1.1rem; font-weight: 600; letter-spacing: -0.01em; }
.kind {
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--brand);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 0.14rem 0.45rem;
}
.where { color: var(--faint); font-size: 0.76rem; margin-left: auto; }
.impact { height: 4px; background: var(--sunken); border-radius: 2px; overflow: hidden; margin-bottom: 0.65rem; }
.impact i { display: block; height: 100%; background: var(--series); border-radius: 2px; }
.finding p { margin: 0; font-size: 0.9rem; color: var(--muted); }

/* ---- tables ---- */
.scroll { overflow-x: auto; }
table {
  border-collapse: collapse;
  width: 100%;
  min-width: 34rem;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
}
th, td { padding: 0.55rem 0.85rem; text-align: left; border-bottom: 1px solid var(--line-soft); }
th {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--faint);
  font-weight: 600;
  white-space: nowrap;
  border-bottom-color: var(--line);
}
tr:last-child td { border-bottom: none; }
td.r, th.r { text-align: right; }

/* status micro-bar: always paired with its number */
.hit { display: flex; align-items: center; gap: 0.5rem; justify-content: flex-end; }
.hit .track { width: 46px; height: 5px; border-radius: 3px; background: var(--sunken); overflow: hidden; }
.hit .track i { display: block; height: 100%; border-radius: 3px; }
.hit.good .track i { background: var(--good); }
.hit.warn .track i { background: var(--warn); }
.hit.crit .track i { background: var(--crit); }

.share { display: flex; align-items: center; gap: 0.6rem; }
.share .track { flex: 1; height: 5px; border-radius: 3px; background: var(--sunken); overflow: hidden; min-width: 60px; }
.share .track i { display: block; height: 100%; background: var(--series); border-radius: 3px; }

.empty { color: var(--faint); font-size: 0.9rem; margin: 0; }

footer {
  margin-top: 3rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 0.78rem;
}
footer a { color: var(--brand); }
footer p { margin: 0 0 0.4rem; }
"""


def escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _hit_class(rate: float) -> str:
    if rate >= HIT_RATE_GOOD:
        return "good"
    if rate >= HIT_RATE_WARN:
        return "warn"
    return "crit"


def _daily_series(report: Report) -> list[tuple[str, float]]:
    """Every date from first to last activity, with quiet days as zero.

    ``report.daily`` only holds days that had turns. Plotting it directly would
    put an idle week and an idle day the same distance apart, so the time axis
    has to be filled in before it can be read as a time axis.
    """
    if not report.daily:
        return []
    dates = sorted(date.fromisoformat(d) for d in report.daily)
    span = (dates[-1] - dates[0]).days
    return [
        (
            (dates[0] + timedelta(days=offset)).isoformat(),
            report.daily.get((dates[0] + timedelta(days=offset)).isoformat(), 0.0),
        )
        for offset in range(span + 1)
    ]


def _chart(report: Report) -> str:
    days = _daily_series(report)
    if not days:
        return '<p class="empty">No dated turns in this window.</p>'

    peak_day, peak_cost = max(days, key=lambda kv: kv[1])
    scale = peak_cost or 1.0
    columns = "".join(
        f'<div class="col{" peak" if day == peak_day else ""}" '
        f'title="{escape(day)} — ${cost:,.2f}">'
        # A quiet day draws no mark at all; a 2px stub would read as spend.
        + (
            f'<i class="fill" style="height:{max(1.5, cost / scale * 100):.1f}%"></i>'
            if cost > 0
            else ""
        )
        + "</div>"
        for day, cost in days
    )
    return (
        f'<div class="chart-frame">'
        f'<div class="chart-head"><span>daily cost, USD</span>'
        f'<span class="num">peak ${peak_cost:,.2f} on {escape(peak_day)}</span></div>'
        f'<div class="plot">{columns}</div>'
        f'<div class="axis"><span class="num">{escape(days[0][0])}</span>'
        f'<span class="num">{escape(days[-1][0])}</span></div>'
        f"</div>"
    )


def _findings(report: Report, limit: int) -> str:
    shown = report.findings[:limit]
    if not shown:
        return '<p class="empty">Nothing flagged.</p>'

    widest = max(f.waste for f in shown) or 1.0
    blocks = []
    for finding in shown:
        label = LABELS.get(finding.kind, finding.kind)
        blocks.append(
            f'<article class="finding">'
            f'<div class="finding-top">'
            f'<span class="amount num">${finding.waste:,.2f}</span>'
            f'<span class="kind">{escape(label)}</span>'
            f'<span class="where">{escape(finding.scope)} · {escape(finding.project)}</span>'
            f"</div>"
            f'<div class="impact"><i style="width:{finding.waste / widest * 100:.1f}%"></i></div>'
            f"<p>{escape(finding.detail)}</p>"
            f"</article>"
        )
    return "".join(blocks)


def _sessions(sessions: Sequence[Session]) -> str:
    if not sessions:
        return '<p class="empty">No sessions in this window.</p>'

    rows = []
    for s in sessions:
        css = _hit_class(s.cache_hit_rate)
        rows.append(
            f"<tr>"
            f'<td class="mono">{escape(s.short_id)}</td>'
            f"<td>{escape(s.project[-44:])}</td>"
            f'<td class="r num">{s.n_turns:,}</td>'
            f'<td class="r num">{s.peak_context:,}</td>'
            f'<td class="r"><span class="hit {css}">'
            f'<span class="track"><i style="width:{s.cache_hit_rate * 100:.0f}%"></i></span>'
            f'<span class="num">{s.cache_hit_rate:.0%}</span></span></td>'
            f'<td class="r num">${s.cost:,.2f}</td>'
            f"</tr>"
        )
    return (
        '<div class="scroll"><table>'
        '<thead><tr><th>session</th><th>project</th><th class="r">turns</th>'
        '<th class="r">peak context</th><th class="r">cache hit</th>'
        '<th class="r">cost</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _models(report: Report) -> str:
    if not report.by_model:
        return '<p class="empty">No model usage recorded.</p>'

    ranked = sorted(report.by_model.items(), key=lambda kv: -kv[1])
    widest = ranked[0][1] or 1.0
    rows = "".join(
        f"<tr>"
        f'<td class="mono">{escape(model)}</td>'
        f'<td><span class="share">'
        f'<span class="track"><i style="width:{cost / widest * 100:.1f}%"></i></span>'
        f"</span></td>"
        f'<td class="r num">${cost:,.2f}</td>'
        f"</tr>"
        for model, cost in ranked
    )
    return (
        '<div class="scroll"><table>'
        "<thead><tr><th>model</th><th>share of spend</th>"
        '<th class="r">cost</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def build(report: Report, top: int = 10) -> str:
    totals = report.totals
    generated = report.generated_at or datetime.now()
    window = f"last {report.days} days" if report.days else "all available history"

    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>proctor — usage audit</title>
<style>{_CSS}</style>
<header>
  <h1>proctor<span class="tick">_</span> usage audit</h1>
  <p class="sub">{escape(window)} · {len(report.sessions)} sessions ·
  {totals.turns:,} API turns · generated {generated:%Y-%m-%d %H:%M} UTC</p>
</header>

<div class="headline">
  <div class="hero">
    <span class="figure num">${report.flagged_waste:,.2f}</span>
    <span class="label">flagged waste</span>
    <span class="of">of ${totals.cost:,.2f} estimated spend
    ({report.waste_share:.0%})</span>
  </div>
  <div class="tiles">
    <div class="tile">
      <span class="figure num">{totals.cache_hit_rate:.0%}</span>
      <span class="label">cache hit rate</span>
    </div>
    <div class="tile">
      <span class="figure num">{totals.output_tokens:,}</span>
      <span class="label">output tokens</span>
    </div>
    <div class="tile">
      <span class="figure num">{len(report.findings)}</span>
      <span class="label">findings</span>
    </div>
  </div>
</div>

<h2>Daily burn</h2>
{_chart(report)}

<h2>Findings, ranked by estimated waste</h2>
{_findings(report, top * 2)}

<h2>Top sessions by cost</h2>
{_sessions(report.sessions[:top])}

<h2>Spend by model</h2>
{_models(report)}

<footer>
<p>Costs are estimated from per-turn usage at list prices and will not match an
invoice. Findings overlap, so flagged waste can exceed total spend — rank by
individual finding rather than reading the total as a recoverable sum.</p>
<p>This file contains excerpts of your own prompts. Treat it as private.
Generated by <a href="https://github.com/GauthamPrabhuM/proctor">proctor</a>.</p>
</footer>"""


def render(report: Report, path: Path, top: int = 10) -> None:
    Path(path).write_text(build(report, top), encoding="utf-8")
