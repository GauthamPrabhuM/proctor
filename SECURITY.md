# Security

## Reporting a vulnerability

Open a [private security advisory](https://github.com/GauthamPrabhuM/proctor/security/advisories/new)
rather than a public issue. Expect an acknowledgement within a few days.

## What proctor touches

**Reads:** `*.jsonl` files under the paths you point it at (default
`~/.claude/projects`), and an optional JSON config file.

**Writes:** stdout, and the `--html` file if you ask for one.

**Network:** none, unless you pass `--admin` or `--admin-only`. Those make `GET`
requests to `api.anthropic.com` against two endpoints —
`/v1/organizations/usage_report/messages` and `/v1/organizations/cost_report` —
and never write. proctor has no other network code.

## Credentials

`ANTHROPIC_ADMIN_KEY` is read from the environment only. proctor never prompts
for it, never persists it, and never writes it to any output. It is validated for
the `sk-ant-admin` prefix before use so a regular API key is not sent by mistake.

An admin key is an organization-wide credential. If you only want the local
audit, don't set it — proctor works fully without one.

## Report contents are sensitive

Reports contain excerpts of your own prompts (up to 110 characters per finding),
along with project paths, session ids, and tool arguments such as file paths and
shell commands. This is deliberate — a finding you cannot identify is not
actionable — but it means:

- **`--json` and `--html` output should be treated as private.** Read a report
  before pasting it into an issue, a chat, or a CI log.
- In CI, prefer the exit code (`--fail-over`) over publishing the report itself.

The HTML report is fully self-contained: no external scripts, stylesheets, fonts,
or images, so opening it makes no network requests. All interpolated values are
HTML-escaped.

## Supported versions

Fixes land on the latest minor release. There is no long-term support branch.
