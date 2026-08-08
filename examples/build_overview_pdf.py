#!/usr/bin/env python3
"""Build ``docs/proctor-overview.pdf`` — the illustrated overview of the tool.

The PDF exists so someone can see what proctor produces without installing it.
Everything in it is generated from the synthetic corpus in ``generate_demo.py``,
never from real transcripts, so it can be committed without leaking prompts.

    python examples/generate_demo.py /tmp/proctor-demo
    python examples/build_overview_pdf.py /tmp/proctor-demo

Requires Google Chrome for rendering (screenshots and PDF). Chrome is the only
external tool involved; proctor itself still has no runtime dependencies.
"""

from __future__ import annotations

import argparse
import base64
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit("Chrome or Chromium is required to render the PDF; none found.")


def run_chrome(chrome: str, *args: str) -> None:
    subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--hide-scrollbars", *args],
        check=True,
        capture_output=True,
    )


def shoot(chrome: str, html: Path, png: Path, width: int, height: int) -> None:
    run_chrome(
        chrome,
        "--force-device-scale-factor=2",
        f"--window-size={width},{height}",
        f"--screenshot={png}",
        html.resolve().as_uri(),
    )


def trim(png: Path, pad: int = 24) -> None:
    """Crop uniform background off a screenshot, in place.

    Chrome captures the whole window, so a short section leaves a band of empty
    background below it. Cropping to the content keeps the figures in the PDF
    from carrying dead space.
    """
    from PIL import Image, ImageChops

    with Image.open(png) as raw:
        image = raw.convert("RGB")
        background = Image.new("RGB", image.size, image.getpixel((1, 1)))
        box = ImageChops.difference(image, background).getbbox()
        if box is None:
            return
        left, upper, right, lower = box
        left, upper = max(0, left - pad), max(0, upper - pad)
        right = min(image.width, right + pad)
        lower = min(image.height, lower + pad)
        image.crop((left, upper, right, lower)).save(png)


def data_uri(png: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


def split_report(report_html: str) -> tuple[str, dict[str, str]]:
    """Return the report's <style> block and its body split by <h2> section."""
    style = re.search(r"<style>.*?</style>", report_html, re.S)
    if not style:
        raise SystemExit("could not find the report's <style> block")

    body = report_html[style.end() :]
    body = re.split(r"<footer>", body)[0]
    header = body[: body.find("<h2>")] if "<h2>" in body else body

    sections: dict[str, str] = {"header": header}
    parts = re.split(r"(?=<h2>)", body)
    for part in parts:
        match = re.match(r"<h2>(.*?)</h2>", part, re.S)
        if match:
            sections[match.group(1).strip().lower()] = part
    return style.group(0), sections


def page(style: str, inner: str) -> str:
    return (
        '<!doctype html><html data-theme="light"><meta charset="utf-8">'
        f"{style}<body>{inner}</body></html>"
    )


def build_screenshots(chrome: str, report_html: str, out: Path) -> dict[str, Path]:
    style, sections = split_report(report_html)
    out.mkdir(parents=True, exist_ok=True)

    def find(*needles: str) -> str:
        for key, value in sections.items():
            if all(n in key for n in needles):
                return value
        return ""

    plan = [
        ("overview", sections["header"] + find("daily", "burn"), 1120, 940),
        ("findings", find("findings"), 1120, 1500),
        ("tables", find("sessions") + find("model"), 1120, 900),
    ]

    shots: dict[str, Path] = {}
    for name, inner, width, height in plan:
        if not inner.strip():
            continue
        html_path = out / f"{name}.html"
        html_path.write_text(page(style, inner), encoding="utf-8")
        png_path = out / f"{name}.png"
        shoot(chrome, html_path, png_path, width, height)
        trim(png_path)
        shots[name] = png_path
    return shots


TERMINAL_CSS = """
body { margin:0; background:#0d1614; padding:26px 30px; }
pre { margin:0; color:#cfdcd7; font:13px/1.62 ui-monospace,SFMono-Regular,Menlo,monospace;
      white-space:pre; font-variant-numeric:tabular-nums; }
"""


def terminal_shot(chrome: str, text: str, out: Path, height: int) -> Path:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_path = out / "terminal.html"
    html_path.write_text(
        f'<!doctype html><meta charset="utf-8"><style>{TERMINAL_CSS}</style>'
        f"<pre>{escaped}</pre>",
        encoding="utf-8",
    )
    png = out / "terminal.png"
    shoot(chrome, html_path, png, 1100, height)
    # Keep a margin of terminal background: cropping tight to the glyphs makes
    # the last line read as though it were cut off.
    trim(png, pad=22)
    return png


DOC_CSS = """
@page { size: A4; margin: 15mm 14mm 16mm; }
* { box-sizing: border-box; }
body { margin:0; color:#12211d; background:#fff;
       font:10.5pt/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.mono { font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
h1 { font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
     font-size:23pt; letter-spacing:-0.02em; margin:0 0 6pt; font-weight:600; }
h1 .tick { color:#16584a; }
h2 { font-size:12pt; margin:0 0 7pt; letter-spacing:-0.01em; }
h3 { font-size:8pt; text-transform:uppercase; letter-spacing:.13em; color:#7c8d88;
     margin:0 0 8pt; font-weight:600; }
p { margin:0 0 8pt; max-width:62em; }
.lede { font-size:12pt; line-height:1.5; color:#3d4c48; max-width:44em; }
.rule { border:0; border-top:1px solid #dfe5e3; margin:14pt 0; }
section { margin-bottom:16pt; }
.break { page-break-before:always; }
figure { margin:0 0 10pt; page-break-inside:avoid; }
figure img { display:block; margin:0 auto; max-width:100%; max-height:225mm;
              width:auto; height:auto; border:1px solid #dfe5e3; border-radius:5pt; }
figcaption { font-size:8.5pt; color:#7c8d88; margin-top:5pt; }
.grid { display:grid; grid-template-columns:repeat(3,1fr); gap:9pt; margin:10pt 0 14pt; }
.stat { border:1px solid #dfe5e3; border-radius:5pt; padding:9pt 11pt; }
.stat b { display:block; font-size:17pt; letter-spacing:-0.02em;
          font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.stat span { display:block; font-size:8pt; color:#7c8d88; margin-top:2pt; }
table { border-collapse:collapse; width:100%; font-size:9.5pt; margin-bottom:10pt; }
th,td { text-align:left; padding:5pt 8pt 5pt 0; border-bottom:1px solid #eceff0;
        vertical-align:top; }
th { font-size:7.5pt; text-transform:uppercase; letter-spacing:.09em; color:#7c8d88;
     border-bottom:1px solid #dfe5e3; }
td.k { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:nowrap;
       padding-right:12pt; }
.callout { border-left:2.5pt solid #16584a; background:#f3f7f6; padding:8pt 11pt;
           border-radius:0 4pt 4pt 0; margin:0 0 10pt; }
.callout p:last-child { margin-bottom:0; }
.note { font-size:9pt; color:#7c8d88; margin-top:-4pt; }
footer { margin-top:16pt; padding-top:8pt; border-top:1px solid #dfe5e3;
         font-size:8pt; color:#7c8d88; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:9pt;
       background:#f1f4f3; padding:1pt 3pt; border-radius:2pt; }
"""


def build_document(shots: dict[str, Path], terminal: Path, report) -> str:
    totals = report.totals
    top = report.findings[0] if report.findings else None
    generated = datetime.now(timezone.utc)

    top_row = ""
    if top is not None:
        top_row = (
            f"<tr><td class='k'>${top.waste:,.2f}</td>"
            f"<td>largest single finding — {top.kind.replace('_', ' ')} "
            f"in {top.project}</td></tr>"
        )

    return f"""<!doctype html><html><meta charset="utf-8">
<title>proctor — what it finds and what it saves</title>
<style>{DOC_CSS}</style>
<body>

<h1>proctor<span class="tick">_</span></h1>
<p class="lede">Agentic coding bills by the token, and the expensive mistakes are
invisible in an invoice: a cache that quietly stopped working, a session
re-reading its own history, the same instructions pasted into thirty
conversations. proctor reads the transcripts you already have and puts a dollar
figure on each one.</p>

<hr class="rule">

<section>
<h3>The problem</h3>
<h2>An invoice tells you the total. It doesn't tell you which habit caused it.</h2>
<p>Claude Code writes a full transcript of every session to disk, including the
exact token usage the API reported for each turn. That is enough to reconstruct
where the money went — but nothing reads it. Teams end up guessing, or reacting
only when a monthly bill crosses a threshold.</p>
<p>The costly patterns are structural rather than occasional. Prompt caching is a
prefix match, so a single interpolated timestamp can silently drop the hit rate
across every session. Cache reads bill at 10% of base input, which makes a broken
cache roughly a <b>10x</b> multiplier on the affected context.</p>

<div class="grid">
  <div class="stat"><b>${totals.cost:,.2f}</b><span>estimated spend</span></div>
  <div class="stat"><b>{totals.cache_hit_rate:.0%}</b><span>cache hit rate</span></div>
  <div class="stat"><b>${report.flagged_waste:,.2f}</b><span>flagged waste
  ({report.waste_share:.0%})</span></div>
</div>
<p class="note">Figures from the demonstration corpus used throughout this
document — {len(report.sessions)} sessions, {totals.turns:,} API turns.</p>
</section>

<section class="break">
<h3>The audit</h3>
<h2>Run one command against transcripts you already have</h2>
<figure>
  <img src="{data_uri(terminal)}" alt="proctor terminal output">
  <figcaption>Terminal output. Demonstration corpus — every figure below comes
  from the same synthetic dataset, generated by
  <code>examples/generate_demo.py</code>, never from real transcripts.</figcaption>
</figure>
</section>

<section class="break">
<h3>What it reports</h3>
<h2>Spend, cache health, and a ranked worklist</h2>
<figure>
  <img src="{data_uri(shots["overview"])}" alt="proctor HTML report — summary and daily burn">
  <figcaption>The HTML report leads with flagged waste against total spend, then
  the daily burn. A single self-contained file that opens offline.</figcaption>
</figure>
</section>

<section class="break">
<h3>Findings</h3>
<h2>Ranked by dollars, so you know where to start</h2>
<p>Each finding names the session, explains the mechanism, and estimates what it
cost. The bar under each amount encodes its share of the largest finding, so the
worklist is scannable before it is read.</p>
<figure>
  <img src="{data_uri(shots["findings"])}" alt="proctor findings, ranked by estimated waste">
  <figcaption>Findings from the demonstration corpus.</figcaption>
</figure>
</section>

<section class="break">
<h3>Attribution</h3>
<h2>Which sessions, which projects, which models</h2>
<figure>
  <img src="{data_uri(shots["tables"])}" alt="proctor session and model tables">
  <figcaption>Cache hit rate carries a status colour beside the number, so a
  failing session is visible without reading every row.</figcaption>
</figure>
</section>

<section class="break">
<h3>Where the value is</h3>
<h2>Three things this changes</h2>

<table>
<tr><th>Use</th><th>What it gives you</th></tr>
<tr><td class="k">Find the leak</td>
    <td>Cache misses are the single most expensive failure and the least visible.
    proctor names the session, the token count, and the likely cause — an edited
    system prompt, a changed tool set, a lapsed TTL.</td></tr>
<tr><td class="k">Set a budget</td>
    <td><code>--fail-over</code> exits non-zero when flagged waste crosses a
    threshold, so a nightly job fails loudly before a bill does. Exit 1 is over
    budget; exit 2 means the audit could not run.</td></tr>
<tr><td class="k">Attribute spend</td>
    <td>Per-project and per-model breakdowns turn "our Claude bill went up" into
    a specific session on a specific day.</td></tr>
</table>

<h3>From this run</h3>
<table>
<tr><th>Figure</th><th>Meaning</th></tr>
<tr><td class="k">{totals.cache_hit_rate:.0%}</td>
    <td>cache hit rate across {totals.turns:,} API turns</td></tr>
<tr><td class="k">${report.flagged_waste:,.2f}</td>
    <td>flagged waste, {report.waste_share:.0%} of ${totals.cost:,.2f} estimated spend</td></tr>
{top_row}
</table>

<div class="callout">
<p><b>On the numbers.</b> Token counts are exact — they come from the usage
object the API returns per turn. Costs are estimates at list prices and will not
match an invoice; they ignore negotiated rates and subscription plans. Waste
figures use deliberately conservative recovery factors, and findings overlap, so
flagged waste can exceed total spend. Rank by individual finding rather than
reading the total as a recoverable sum.</p>
</div>
</section>

<section>
<h3>Practicalities</h3>
<table>
<tr><td class="k">Install</td><td><code>pip install proctor-audit</code></td></tr>
<tr><td class="k">Requires</td><td>Python 3.9+. No runtime dependencies.</td></tr>
<tr><td class="k">Data</td><td>Runs locally. No network calls unless you ask for the
organization report.</td></tr>
<tr><td class="k">Privacy</td><td>Reports quote your prompts, so treat JSON and HTML
output as private.</td></tr>
</table>
</section>

<footer>
Generated {generated:%Y-%m-%d} from the demonstration corpus in
<span class="mono">examples/generate_demo.py</span>. Reproduce with
<span class="mono">python examples/build_overview_pdf.py</span>.
github.com/GauthamPrabhuM/proctor · MIT
</footer>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "logs",
        type=Path,
        nargs="?",
        default=Path("/tmp/proctor-demo"),
        help="demo corpus directory (default: /tmp/proctor-demo)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "docs" / "proctor-overview.pdf",
        help="where to write the PDF",
    )
    args = parser.parse_args()

    if not args.logs.exists():
        sys.exit(f"{args.logs} not found — run examples/generate_demo.py first.")

    sys.path.insert(0, str(REPO / "src"))
    import io

    from proctor.analyze import audit
    from proctor.render import html as html_render
    from proctor.render import terminal as terminal_render
    from proctor.term import Style
    from proctor.transcripts import load

    days = 90
    since = datetime.now(timezone.utc) - timedelta(days=days)
    report = audit(load([args.logs], since), days=days, top=10)

    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        buffer = io.StringIO()
        terminal_render.render(report, buffer, Style(False), top=6)
        text = buffer.getvalue().strip("\n")
        # Trim to the sections that fit a page without shrinking the type.
        lines = text.splitlines()
        cut = next(
            (i for i, ln in enumerate(lines) if "top sessions by cost" in ln), len(lines)
        )
        # Capture generously; trim() crops back to the content, so an over-tall
        # window is free while a short one silently clips the last lines.
        terminal_png = terminal_shot(chrome, "\n".join(lines[:cut]).rstrip(), work, 1000)

        shots = build_screenshots(chrome, html_render.build(report, top=10), work)
        missing = {"overview", "findings", "tables"} - set(shots)
        if missing:
            sys.exit(f"could not build screenshots for: {sorted(missing)}")

        doc = work / "document.html"
        doc.write_text(build_document(shots, terminal_png, report), encoding="utf-8")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        run_chrome(
            chrome,
            "--no-pdf-header-footer",
            f"--print-to-pdf={args.out.resolve()}",
            doc.resolve().as_uri(),
        )

    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
