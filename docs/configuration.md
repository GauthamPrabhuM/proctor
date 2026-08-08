# Configuration

proctor needs no configuration. A config file exists for one reason: so that a
price change does not require editing the source or waiting for a release.

## Where it looks

In order, first match wins:

1. `--config /path/to/file.json`
2. `$PROCTOR_CONFIG`
3. `$XDG_CONFIG_HOME/proctor/config.json` (default: `~/.config/proctor/config.json`)

A missing file is not an error unless you named it explicitly. A malformed one
always is — silently falling back to defaults after you asked for an override
would be worse than failing.

## Schema

```json
{
  "log_dirs": ["~/.claude/projects", "/mnt/shared/claude-logs"],
  "days": 14,
  "top": 20,
  "pricing": {
    "opus-5": { "input": 5.0, "output": 25.0 },
    "my-finetune": {
      "input": 1.0,
      "output": 4.0,
      "cache_write_5m": 1.25,
      "cache_write_1h": 2.0,
      "cache_read": 0.1
    }
  }
}
```

| Key | Type | Effect |
|---|---|---|
| `log_dirs` | string or list | Default transcript paths. Command-line paths override it. |
| `days` | integer | Default window. `--days` overrides it. |
| `top` | integer | Default rows per table. `--top` overrides it. |
| `pricing` | object | Price overrides, keyed by model-id substring. |

Command-line flags always beat the config file.

## Pricing overrides

Keys are **lowercase substrings matched against the model id**, and overrides are
tested *before* the built-in rules — so `"opus"` overrides every Opus-tier model,
while `"opus-5"` overrides only that one.

`input` and `output` are required and are dollars per million tokens. The three
cache rates are optional; when omitted they are derived from `input` using the
published multipliers:

| Rate | Multiplier |
|---|---|
| 5-minute cache write | 1.25 × input |
| 1-hour cache write | 2.0 × input |
| Cache read | 0.1 × input |

Supply all three explicitly only if a model departs from those ratios.

### Modelling a discount

To see what your usage looks like at a negotiated rate, scale the published
prices:

```json
{ "pricing": { "opus": { "input": 4.0, "output": 20.0 } } }
```

To rank sessions by token volume alone, set every price to `1.0` — the "cost"
column then reads as millions of tokens.

## Built-in prices

The defaults are list prices as of August 2026, in `src/proctor/pricing.py`. They
cover the Claude 5 family (Fable, Mythos, Opus, Sonnet), the Opus 4.x line, the
Sonnet 4.x line, and the Haiku models, including the Sonnet 5 introductory rate
and its 2026-09-01 expiry.

An unrecognised model id falls back to Sonnet standard rates ($3/$15). If you see
an unfamiliar model in the `spend by model` table, that is the rate it was
costed at — add an override until the built-in table catches up.
