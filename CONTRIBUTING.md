# Contributing

## Setup

```bash
git clone https://github.com/GauthamPrabhuM/proctor
cd proctor
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

All three run in CI and should pass before you open a pull request:

```bash
pytest                      # test suite
ruff check . && ruff format --check .
mypy
```

`ruff format` will fix formatting for you.

## Two rules that matter

**No runtime dependencies.** proctor is a diagnostic for cost problems; it should
not become one. The standard library covers everything, including the HTML report
and the Admin API client. Development dependencies are fine.

**Python 3.9 is the floor.** `from __future__ import annotations` at the top of
every module makes modern annotation syntax safe. CI tests 3.9 through 3.13.

## Adding a check

Four steps.

**1. Write the detector.** Put it in `src/proctor/detectors/`, grouped with
related checks. Subclass `SessionDetector` for per-session logic or `Detector`
for corpus-wide logic.

```python
class MyDetector(SessionDetector):
    kind = "my_check"          # stable id: appears in --only/--skip and JSON
    label = "MY CHECK"         # shown in the terminal and HTML report
    summary = "One line for --help."

    def inspect(self, session, ctx):
        if not_interesting:
            return
        yield Finding(
            kind=self.kind,
            waste=estimated_dollars,
            scope=session.short_id,
            project=session.project,
            detail="What happened, why it costs money, and what to do about it.",
            evidence={"backing": "numbers"},
        )
```

**2. Register it** in `ALL_DETECTORS` in `src/proctor/detectors/__init__.py`.
Nothing else needs updating — the CLI choices, terminal labels, HTML legend, and
help text all derive from that list. If your finding needs its own HTML tag
colour, add a `--t-<kind>` custom property in `render/html.py`.

**3. Write two tests.** One that trips the check, and one that proves it stays
quiet on a healthy session. The second is the one that matters: an auditor that
cries wolf gets muted, and then it may as well not exist. Build transcripts with
the helpers in `tests/conftest.py` — never commit a captured transcript, they
contain real prompts.

**4. Document it** in `docs/findings.md`: what it means, what trips it, the exact
formula, and when to ignore it.

## Conventions for waste estimates

- **Be conservative.** Where a finding needs a counterfactual, apply a recovery
  factor rather than assuming the ideal case, and say why in a comment.
- **Name your constants.** Thresholds go at the top of the module with a
  docstring explaining the number — not inline as a bare literal.
- **Say which numbers are approximate.** Anything derived from the
  four-characters-per-token estimate is an approximation, and `docs/findings.md`
  should label it as one.
- **Findings should be actionable.** `detail` should tell the reader what to
  change, not just that something is expensive.

## Pricing updates

Prices live in `DEFAULT_RULES` in `src/proctor/pricing.py`. Rules are ordered
substring matches — a specific pattern must come before a general one, which is
why `opus-4-1` precedes `opus`. Add a `PriceRule` and a case to
`tests/test_pricing.py`; the parametrized test there is the regression net for
exactly this.
