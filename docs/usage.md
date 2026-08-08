# Using proctor

A task-oriented guide. For the reference material, see
[`findings.md`](findings.md) (what each check means),
[`configuration.md`](configuration.md) (pricing overrides), and
[`json-schema.md`](json-schema.md) (the `--json` contract).

---

## Install

Python 3.9 or newer. No runtime dependencies.

```bash
pip install proctor-audit
```

Or from a checkout:

```bash
git clone https://github.com/GauthamPrabhuM/proctor
cd proctor
pip install -e .
```

Both give you a `proctor` command. Without installing, `python -m proctor` works
from the repository root.

---

## Your first run

```bash
proctor
```

That audits `~/.claude/projects` over the last 30 days. Nothing leaves your
machine.

```
proctor — 5 sessions, 1,443 API turns  (last 30 days)

  estimated spend                     $648.47
  input (uncached)             84,084 tok
  cache writes             19,760,578 tok
  cache reads             515,275,416 tok   (hit rate 96%)
  output                    1,375,741 tok
  flagged waste                         $4.31  (1% of spend)
```

### Reading the header

| Line | What to make of it |
|---|---|
| **estimated spend** | List-price estimate for the window. Not an invoice — see [Accuracy](#accuracy). |
| **input (uncached)** | Tokens paid at full rate. You want this small relative to cache reads. |
| **cache writes** | Tokens written into the cache at 1.25× base input. Unavoidable and cheap. |
| **cache reads** | Tokens served at 10% of base input. **This should dominate.** |
| **hit rate** | Cache reads ÷ all input. Above ~90% on long sessions is healthy; under 50% means something is invalidating your prefix. |
| **output** | Tokens generated. The only line that represents work produced rather than context carried. |
| **flagged waste** | Sum of all findings. Can exceed spend — findings overlap. |

The single most useful number is the **hit rate**. Everything else is downstream
of it.

### Then read the findings

```
findings, ranked by estimated waste

  [$1.09] COLD CONTEXT  (s1, /tmp/demo)
      3 turn(s) reprocessed 270,000 uncached input tokens mid-session. Cache
      reads cost 10% of base input, so this context was paid for at full price a
      second time. Usually caused by editing the system prompt, changing the tool
      set, or letting the 5-minute cache TTL lapse between turns.
```

Findings are sorted by dollar impact, so work top-down and stop when the numbers
stop mattering. The bracketed session id matches the `session` column in the
`top sessions by cost` table above it.

---

## Common tasks

### "What did last week cost me?"

```bash
proctor --days 7
```

### "Which project is expensive?"

Read the `top sessions by cost` table, then narrow:

```bash
proctor --project my-app --days 30
```

`--project` is a case-insensitive substring match against the project path.

### "Show me everything, not just the top rows"

```bash
proctor --top 50
```

`--top` controls rows per table and how many findings the terminal prints (it
prints `2 × top`). `--json` always contains every finding regardless.

### "I only care about caching"

```bash
proctor --only cache_miss
proctor --only cache_miss --only balloon      # repeatable
```

Or exclude the noisy ones:

```bash
proctor --skip fat_prompt --skip model_mismatch
```

Valid kinds: `cache_miss`, `balloon`, `repeat`, `redundant_tool`, `fat_prompt`,
`sidechain`, `model_mismatch`. `proctor --help` lists them with one-line
descriptions.

### "Audit logs from somewhere else"

```bash
proctor /path/to/logs                          # a directory, walked recursively
proctor ~/.claude/projects /mnt/team-logs      # several paths
proctor ~/.claude/projects/-Users-me-app/abc123.jsonl   # one session file
```

### "Give me something I can share"

```bash
proctor --html report.html
```

A single self-contained file — no external scripts, fonts, or images, so it opens
offline and makes no network requests. It follows your system light/dark theme.

**It contains excerpts of your prompts.** Read it before sending it anywhere.

---

## Scripting with `--json`

`--json` writes one object to stdout; diagnostics go to stderr, so stdout is
always either valid JSON or empty. The shape is versioned — see
[`json-schema.md`](json-schema.md).

```bash
# Top five findings, tab-separated
proctor --json | jq -r '.findings[:5][] | "\(.estimated_waste_usd)\t\(.kind)"'

# Total waste attributable to cache misses
proctor --json | jq '[.findings[] | select(.kind == "cache_miss")
                      | .estimated_waste_usd] | add'

# Sessions with a poor cache hit rate
proctor --json | jq -r '.sessions[] | select(.cache_hit_rate < 0.8) | .id'

# Spend per model, largest first
proctor --json | jq -r '.by_model | to_entries | sort_by(-.value)[]
                        | "\(.value)\t\(.key)"'
```

Parse `evidence`, never `detail`. `detail` is prose written for humans and its
wording changes between releases; `evidence` holds the same numbers as typed
fields.

---

## Using it in CI

`--fail-over` makes proctor a budget gate. It exits `1` when flagged waste
exceeds the threshold.

```bash
proctor --days 1 --fail-over 5.00
```

| Exit code | Meaning |
|---|---|
| `0` | Audit ran; flagged waste stayed at or under `--fail-over` |
| `1` | Flagged waste exceeded `--fail-over` |
| `2` | Usage error — bad flag, no transcripts found, unreadable config, or an Admin API failure |

A nightly GitHub Actions job:

```yaml
name: token budget
on:
  schedule: [{ cron: "0 9 * * *" }]
  workflow_dispatch:

jobs:
  audit:
    runs-on: self-hosted        # needs access to the machine's transcripts
    steps:
      - run: pip install proctor-audit
      - run: proctor --days 1 --fail-over 5.00
```

**Do not publish the report itself into CI logs.** Use the exit code. If you need
the detail, write `--json` to a private artifact.

Distinguishing exit `1` from exit `2` matters — the first means you overspent,
the second means the audit never ran:

```bash
proctor --days 1 --fail-over 5.00
case $? in
  0) echo "within budget" ;;
  1) echo "over budget — see findings" ;;
  2) echo "proctor could not run" ; exit 2 ;;
esac
```

---

## Organization-wide reporting

Local transcripts only cover the machine they are on. For org totals, proctor can
read the Anthropic Admin API.

```bash
export ANTHROPIC_ADMIN_KEY=sk-ant-admin-...
proctor --admin --days 30        # org report, then the local audit
proctor --admin-only --days 30   # org report only
```

Output is per-model token consumption, cache hit rates, billed cost, and an
advisory line for any model with a hit rate under 50%:

```
org usage — last 30 days (Admin API)

  model                        uncached in    cache read   cache write     output    hit
  claude-opus-5                  4,000,000     1,000,000       500,000    200,000    18%

  billed org cost over period: $123.45

  ! claude-opus-5: cache hit rate 18% — better caching could save roughly $12.60
    over this period.
```

Unlike the local audit's estimates, **billed org cost is the real figure** — it
comes from Anthropic's cost report, not from list-price arithmetic.

Notes on the key:

- It must start with `sk-ant-admin`. A regular API key is rejected before any
  request is sent, so you can't leak one by mistake.
- proctor reads it from the environment only — never prompts, never persists it,
  never writes it to output.
- It is an organization-wide credential. If you only want the local audit, don't
  set it; everything else works without one.
- proctor issues `GET` requests to exactly two endpoints and never writes.

---

## Configuration

proctor needs no configuration. A config file exists so a price change doesn't
require editing source. It is looked for at `--config`, then `$PROCTOR_CONFIG`,
then `~/.config/proctor/config.json`.

```json
{
  "log_dirs": ["~/.claude/projects"],
  "days": 14,
  "top": 20,
  "pricing": {
    "opus": { "input": 4.0, "output": 20.0 }
  }
}
```

Command-line flags always win over the file. Full schema in
[`configuration.md`](configuration.md).

Two things this is good for:

**Modelling a discount.** Set your negotiated rates and re-run; the ranking
changes if your discount is uneven across model tiers.

**Ranking by token volume instead of cost.** Override every Claude model to a
price of `1.0` and the cost column reads as millions of tokens — useful on a
subscription plan where dollar figures aren't meaningful:

```json
{ "pricing": { "claude": { "input": 1.0, "output": 1.0 } } }
```

The pattern `claude` matches every Claude model id, and overrides are tested
before the built-in rules, so this replaces the whole table.

---

## As a library

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from proctor import audit, load

since = datetime.now(timezone.utc) - timedelta(days=7)
corpus = load([Path("~/.claude/projects").expanduser()], since)
report = audit(corpus, days=7)

print(f"${report.totals.cost:.2f} spent, ${report.flagged_waste:.2f} flagged")
print(f"cache hit rate: {report.totals.cache_hit_rate:.0%}")

for finding in report.findings[:5]:
    print(f"  ${finding.waste:6.2f}  {finding.kind:16} {finding.scope}")
```

Run a subset of checks, or use your own prices:

```python
from proctor import PriceBook
from proctor.detectors import select

report = audit(
    corpus,
    prices=PriceBook.from_overrides({"opus": {"input": 4.0, "output": 20.0}}),
    detectors=select(only=["cache_miss", "balloon"]),
    top=25,
)
```

`Report`, `Session`, `Finding`, `Turn`, and `PriceBook` are the supported
surface; everything else is internal and may change between minor releases.

---

## Interpreting what you find

### A low cache hit rate

The most valuable thing proctor can tell you. Caching is a **prefix match** —
any byte change invalidates everything after it. Usual culprits, in order of how
often they turn out to be the cause:

1. A timestamp, UUID, or session id interpolated near the top of the system
   prompt. Every request is then a unique prefix.
2. The tool set changing mid-conversation. Tools render at position 0, so
   adding or reordering one invalidates the whole prompt.
3. Gaps longer than the 5-minute cache TTL between turns.
4. Non-deterministic serialization — `json.dumps` without `sort_keys=True`, or
   iterating a `set`.

### High spend with a *good* hit rate

Caching is working; the context is simply large. Look at the `peak ctx` column.
If sessions are running past a few hundred thousand tokens, the fix is
compaction or shorter sessions, not caching.

### Flagged waste above 100% of spend

Expected. A single bad session trips several checks that each blame the same
tokens — a ballooned session is usually also a cold-context session. Rank by
individual finding; the total is not a recoverable sum.

### No findings at all

Genuinely possible, and common on well-cached workloads. proctor deliberately
does not flag near-threshold cases: findings worth under a cent are dropped,
and every check has a floor documented in [`findings.md`](findings.md). An
auditor that cries wolf gets muted.

---

## Accuracy

**Exact:** token counts on assistant turns. They come from the `usage` object the
API returned for each request.

**Estimated:** everything else.

- **Costs are list prices.** They ignore negotiated rates, batch discounts, and
  subscription plans. On a Claude Code Max or Team plan, treat dollar figures as
  *relative* signal for ranking sessions, not for reconciling a bill.
- **Prompt and tool-result sizes** are approximated at four characters per token,
  because the API reports usage per request, not per content block. The
  oversized-prompt, repeated-context, and redundant-tool-call findings depend on
  this.
- **Waste figures use fixed recovery factors** rather than claiming a perfect
  counterfactual, and are deliberately conservative. Every coefficient is in
  [`findings.md`](findings.md).

---

## Privacy

proctor makes no network calls unless you pass `--admin` or `--admin-only`.

Reports contain excerpts of your prompts (up to 110 characters per finding),
project paths, session ids, and tool arguments including file paths and shell
commands. That's deliberate — a finding you can't identify isn't actionable — but
it means **`--json` and `--html` output is private**. Review before sharing, and
in CI prefer the exit code over publishing the report.

---

## Troubleshooting

**`no transcript path found`** (exit 2) — the default `~/.claude/projects`
doesn't exist. Pass your log directory explicitly.

**`no API turns found in the last N days`** (exit 2) — the path exists but holds
nothing recent. proctor skips files by mtime before opening them, so widen the
window: `proctor --days 365`.

**An unfamiliar model in `spend by model`** — an unrecognised model id falls back
to Sonnet standard rates ($3/$15). Add a pricing override until the built-in
table catches up.

**A project path looks wrong** — Claude Code encodes `/Users/me/my-app` as
`-Users-me-my-app`, so a hyphen in a real directory name decodes as a separator
(`/Users/me/my/app`). Cosmetic; it doesn't affect any figure.

**`--days 0` is rejected** — deliberately. It used to be silently treated as the
default, which hid the mistake.
