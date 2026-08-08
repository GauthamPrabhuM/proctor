# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-08-08

Restructured from a single script into an installable, tested package. Behaviour
is a superset of 0.1.0; the terminal output is recognisably the same report.

### Added

- Installable package with a `proctor` console script and a supported library API
  (`proctor.load`, `proctor.audit`).
- **Redundant tool call** detection — identical tool calls repeated inside one
  session, sized by the result they pulled back into context.
- `--only` / `--skip` to select finding kinds, `--project` to filter by project
  path, and `--fail-over USD` to gate CI on flagged waste.
- Documented exit codes: `0` clean, `1` over budget, `2` usage error.
- Optional JSON config file for pricing and defaults (`--config`,
  `$PROCTOR_CONFIG`, `~/.config/proctor/config.json`).
- `--admin-only` for an org report without the local audit.
- Colour output honouring `NO_COLOR` and non-TTY stdout, plus a daily-burn
  sparkline.
- `schema_version` on JSON output, an `evidence` object on every finding, and a
  documented contract in `docs/json-schema.md`.
- Test suite (88 tests) covering parsing, pricing, every detector's positive and
  negative case, the CLI, and the Admin API client.
- CI across Python 3.9–3.13 on Linux, macOS, and Windows, with lint, type check,
  and an end-to-end smoke test.
- Documentation: architecture, per-finding methodology, configuration, JSON
  schema, and contributing guides.

### Fixed

- `claude-3-opus` was priced at $5/$25 instead of $15/$75 — it matched the
  generic `opus` rule meant for Opus 4.5 and later.
- Haiku 3 and 3.5 never matched their price rules. The patterns were `haiku-3-5`
  and `haiku-3`, but the model ids read `claude-3-5-haiku-*`; both fell through
  to the Haiku 4.5 rate.
- Project paths lost their leading `/` when decoded from Claude Code's directory
  slug.
- Streaming-retry deduplication was O(n²) in turns per session, which was
  noticeable on long sessions.
- Word wrapping could overflow the column on words longer than the wrap width.
- HTML output did not escape quotation marks.
- Unreadable transcript files aborted the run instead of being reported and
  skipped.

### Changed

- Costs, sessions, and findings are typed dataclasses rather than dictionaries.
- Detectors are independent classes behind a registry, so adding a check touches
  one list.
- Admin API failures now produce actionable messages instead of tracebacks, and
  pagination is bounded.
- `--days 0` is rejected rather than silently treated as the default.

## [0.1.0] — 2026-08-08

Initial release: a single-file auditor covering cold context, ballooned sessions,
repeated context, oversized prompts, subagent burn, and model mismatch, with
terminal, JSON, and HTML output plus an Admin API report.
