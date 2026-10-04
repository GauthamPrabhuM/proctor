# proctor

**A cost auditor for your own agentic workflows.** proctor reads your Claude Code
session transcripts and tells you where tokens are going, with a dollar figure
on every finding, so you can tell a $40 problem from a $0.40 one.

It runs entirely on your machine, has no dependencies beyond the Python standard
library, and never sends your transcripts anywhere.

```
proctor: 5 sessions, 1,425 API turns  (last 30 days)

  estimated spend                     $644.06
  input (uncached)             84,051 tok
  cache writes             19,728,163 tok
  cache reads             508,131,522 tok   (hit rate 96%)
  output                    1,355,236 tok
  flagged waste                        $31.22  (5% of spend)

daily burn
  ▁▁▂▁▁▅█▁▂▁▇▂▅▅▂▁▁▂▁▂
  2026-06-17 → 2026-08-08   peak $138.43/day

findings, ranked by estimated waste

  [$18.40] COLD CONTEXT  (c8c5d228, /Users/me/src/app)
      12 turn(s) reprocessed 1,240,000 uncached input tokens mid-session. Cache
      reads cost 10% of base input, so this context was paid for at full price a
      second time. Usually caused by editing the system prompt, changing the tool
      set, or letting the 5-minute cache TTL lapse between turns.
```

## Install

Python 3.9 or newer. No runtime dependencies.

```bash
pip install proctor-audit
proctor
```

Or run it straight from a checkout:

```bash
git clone https://github.com/GauthamPrabhuM/proctor
cd proctor
python -m proctor
```

## Usage

```bash
proctor                          # audit ~/.claude/projects, last 30 days
proctor --days 7                 # narrow the window
proctor /path/to/logs            # audit specific files or directories
proctor --project my-app         # only sessions from a matching project path
proctor --top 20                 # more rows per table
proctor --html report.html       # also write a standalone dashboard
proctor --json                   # machine-readable output
proctor --only balloon --only repeat   # run selected checks
proctor --skip fat_prompt        # or exclude them
```

Run `proctor --help` for the full flag list and the finding-kind reference.

### Organization-wide reporting

Local transcripts only cover one machine. For org-level totals, proctor can read
the Anthropic Admin API: per-model token consumption, cache hit rates, and
billed cost:

```bash
export ANTHROPIC_ADMIN_KEY=sk-ant-admin-...
proctor --admin --days 30          # org report, then the local audit
proctor --admin-only --days 30     # org report only
```

An admin key is an organization-wide credential. proctor only ever issues `GET`
requests against `/v1/organizations/usage_report/messages` and
`/v1/organizations/cost_report`, and never writes.

### In CI

`--fail-over` turns proctor into a budget gate. It exits `1` when flagged waste
exceeds the threshold, so a nightly job can fail loudly before the bill does:

```bash
proctor --days 1 --fail-over 5.00
```

| Exit code | Meaning |
|---|---|
| `0` | Audit completed and stayed under `--fail-over` |
| `1` | Flagged waste exceeded `--fail-over` |
| `2` | Usage error, no transcripts found, or an Admin API failure |

## What it looks for

| Finding | What it means |
|---|---|
| **Cold context** | Large prompt prefixes reprocessed uncached mid-session. Cache reads cost 10% of base input, so this is the single most expensive mistake available. |
| **Ballooned session** | Context grew past 120k tokens over many turns while producing almost no output. The session is mostly re-reading itself. |
| **Repeated context** | The same large block pasted into several sessions. It belongs in `CLAUDE.md`, a skill, or a file the agent reads on demand. |
| **Redundant tool calls** | The same tool call, byte-identical arguments, issued repeatedly in one session. Each repeat pulls the same result back into context. |
| **Oversized prompt** | A prompt far above your own median. That text rides along in context for every turn that follows it. |
| **Subagent burn** | Sidechains consuming most of a session's cost. Every subagent starts cold and re-derives context the parent already had. |
| **Model mismatch** | A premium model that produced very little output, priced against what the same tokens would cost a tier down. |

Each check, its thresholds, and exactly how its dollar figure is computed are
documented in [`docs/findings.md`](docs/findings.md).

## Using it as a library

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from proctor import audit, load

since = datetime.now(timezone.utc) - timedelta(days=7)
report = audit(load([Path("~/.claude/projects").expanduser()], since), days=7)

print(f"${report.totals.cost:.2f} spent, ${report.flagged_waste:.2f} flagged")
for finding in report.findings[:3]:
    print(finding.kind, round(finding.waste, 2), finding.detail)
```

The JSON output is a versioned contract. See
[`docs/json-schema.md`](docs/json-schema.md).

## How accurate is this?

The token counts are exact: they come from the `usage` object the API returned
for each turn. Everything derived from those counts carries caveats worth stating
plainly:

- **Costs are estimates at list prices.** They will not match an invoice. They
  ignore negotiated rates, batch discounts, and subscription plans. If you are on
  a Claude Code Max or Team plan, treat the dollar figures as *relative* signal:
  useful for ranking sessions, not for reconciling a bill.
- **Prompt and tool-result sizes are approximated** at four characters per token,
  because the API does not report per-block usage. Findings that rely on this
  (oversized prompts, repeated context, redundant tool calls) are approximate.
- **Waste estimates are deliberately conservative** and use fixed recovery
  factors rather than claiming a perfect counterfactual. `docs/findings.md` shows
  every coefficient and the reasoning behind it. They are a prioritized list of
  things to look at, not a refund calculation.
- **Findings can overlap.** A single expensive session may trip several checks
  that each blame the same tokens, so total flagged waste can exceed total spend.
  Rank by individual finding; don't read the total as a recoverable sum.

Prices are current as of August 2026 and can be overridden without touching the
source. See [`docs/configuration.md`](docs/configuration.md).

## Privacy

proctor reads your transcripts locally and makes no network calls unless you
explicitly pass `--admin`.

The reports it writes do contain excerpts of your own prompts (up to 110
characters per finding), because a finding you cannot identify is not actionable.
That means **`--json` and `--html` output should be treated as private**. Do not
paste a report into a public issue without reading it first.

## Documentation

- [`docs/proctor-overview.pdf`](docs/proctor-overview.pdf): illustrated overview
  with real output: what it finds, and where the value is
- [`docs/usage.md`](docs/usage.md): task-oriented guide with worked examples
- [`docs/findings.md`](docs/findings.md): every check, threshold, and formula
- [`docs/architecture.md`](docs/architecture.md): how the pipeline fits together
- [`docs/configuration.md`](docs/configuration.md): pricing and defaults
- [`docs/json-schema.md`](docs/json-schema.md): the `--json` contract
- [`CONTRIBUTING.md`](CONTRIBUTING.md): adding a new check

## Trying it without your own transcripts

`examples/generate_demo.py` writes a deterministic synthetic corpus that trips
every check, so you can see a full report before pointing proctor at anything of
your own:

```bash
python examples/generate_demo.py /tmp/proctor-demo
proctor /tmp/proctor-demo --days 90
```

That same corpus produces every figure in the PDF above. No real prompts are
involved, which is why it can be committed. Regenerate the PDF with
`pip install -e ".[docs]"` (plus Chrome) and
`python examples/build_overview_pdf.py`.

## License

MIT. See [LICENSE](LICENSE).
