# Architecture

proctor is a four-stage pipeline. Each stage has one job and hands typed objects
to the next, so a change in one rarely reaches the others.

```
  transcripts/        analyze.py          detectors/          render/
  *.jsonl      ──▶    Corpus       ──▶    Report       ──▶    terminal
                      + PriceBook          + Findings          json
                                                               html
   parse              cost                 judge               present
```

## Stage 1 — Parse (`transcripts.py`)

Walks `*.jsonl` files, skipping any whose mtime predates the window without
opening them (this is what keeps a full-history `~/.claude/projects` cheap).
Produces a `Corpus`: sessions keyed by id, plus a corpus-wide index of large text
blocks fingerprinted for cross-session duplicate detection.

The transcript format is not a published contract, so parsing is defensive
throughout — an unrecognised record is skipped, a corrupt line is counted and
stepped over, and an unreadable file is reported rather than fatal.

Two subtleties worth knowing:

- **Streaming retries re-emit the same message id.** Turns are indexed by
  `(session_id, message_id)` and the copy with the largest `output_tokens` wins.
  Counting them all would inflate every total.
- **Tool results are matched back to their calls** via `tool_use_id`, which is
  what lets the redundant-call detector know how expensive a repeat actually was.

## Stage 2 — Cost (`pricing.py`, `analyze.py`)

`PriceBook` resolves a model id and timestamp to a `Price`. Rules are ordered
substring matches, so specific patterns (`opus-4-1`) must precede general ones
(`opus`). A rule may carry an `until` date, which is how introductory pricing is
expressed without special-casing it in the cost math.

`analyze.audit()` prices every session, accumulates totals, and hands a read-only
`AuditContext` to the detectors.

## Stage 3 — Judge (`detectors/`)

Each check is a class with a `kind`, a `label`, and a `scan()` method returning
`Finding` objects. `SessionDetector` is a convenience base for the common case of
looking at one session at a time; corpus-wide checks (repeated context, oversized
prompts) subclass `Detector` directly.

The registry in `detectors/__init__.py` is the single source of truth. The
`--only`/`--skip` choices, the terminal labels, the HTML legend, and the `--help`
epilog all derive from it, so adding a check means touching one list.

Detectors never format currency or text for a specific output; they produce a
`detail` string and an `evidence` dict, and the renderers decide presentation.

## Stage 4 — Present (`render/`)

Three renderers over the same `Report`:

- **terminal** — colour-aware (respects `NO_COLOR` and non-TTY stdout)
- **json_report** — a versioned contract, see [`json-schema.md`](json-schema.md)
- **html** — a single self-contained file with no external assets, deliberately:
  the report contains excerpts of your own prompts and should not be fetching
  anything from the network to display them

## Where things deliberately are not

**No config-driven detectors.** Thresholds live as named module constants next to
the logic they govern, with a comment explaining the number. A YAML file of magic
numbers would move them further from their justification.

**No database or cache.** A full audit of ~500M tokens of transcripts takes about
a second. State would be a correctness liability for no measurable gain.

**No dependencies.** proctor is a diagnostic tool for cost problems; it should
not itself be a maintenance cost. The standard library covers everything needed,
including the HTML report and the Admin API client.
