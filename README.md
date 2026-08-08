# proctor

Audits your Claude Code / API usage for token waste — a cost/efficiency auditor for your own agentic workflows.

It reads Claude Code session transcripts (`~/.claude/projects/**/*.jsonl`) and/or the Anthropic Admin API, then tells you where you're burning tokens, with a dollar estimate on every finding:

- **Cold context** — large uncached input reprocessed mid-session that should have been a cache read (cache reads cost 10% of base input)
- **Ballooned sessions** — context snowballs past 120k tokens while producing almost no output
- **Repeated context** — the same block pasted into multiple sessions (belongs in `CLAUDE.md`, a skill, or a file read on demand)
- **Oversized prompts** — prompts far above your median that ride along in context every turn afterward
- **Subagent burn** — sidechains consuming most of a session's cost while re-deriving context the parent already had
- **Model mismatch** — premium models (Opus/Fable) doing light work a cheaper model could handle

## Usage

No dependencies — Python 3.8+, stdlib only.

```bash
python3 proctor.py                     # audit ~/.claude/projects, last 30 days
python3 proctor.py --days 7            # last week only
python3 proctor.py /path/to/logs       # custom log dir(s)
python3 proctor.py --html report.html  # also write an HTML dashboard
python3 proctor.py --json              # machine-readable output
python3 proctor.py --top 15            # rows per table (default 10)
```

### Org-level report (Admin API)

```bash
export ANTHROPIC_ADMIN_KEY=sk-ant-admin-...
python3 proctor.py --admin --days 30
```

Reports per-model token consumption, cache hit rates, and total org cost via `/v1/organizations/usage_report/messages` and `/v1/organizations/cost_report`.

## Example output

```
=== proctor: 6 sessions, 60 API turns ===

  estimated spend      $3.71
  cache reads               3,019,800 tok   (hit rate 71%)
  flagged waste        $1.80 (49% of spend)

--- findings (ranked by estimated waste) ---

  [$0.83] COLD CONTEXT  (bbbb2222, dev/myapp)
      10 turn(s) reprocessed 515,000 uncached input tokens mid-session...

  [$0.35] BALLOONED SESSION  (cccc3333, dev/myapp)
      Context grew to 195,500 tokens over 24 turns while producing only
      2,880 output tokens (1.5%)...
```

## Notes

- Costs are estimated from per-turn usage at list prices (August 2026, including the Sonnet 5 introductory-pricing cutover on 2026-09-01); actual billing may differ. Edit the `PRICING` table at the top of `proctor.py` to update rates.
- Streaming retries that emit duplicate message ids are deduplicated (largest `output_tokens` wins).
- Repeated-context detection fingerprints paragraph-level chunks, so a pasted block is caught even when the text around it differs.

## License

MIT
