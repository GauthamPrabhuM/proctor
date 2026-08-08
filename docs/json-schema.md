# JSON output contract

`proctor --json` emits a single object on stdout. The shape is versioned:
`schema_version` increments only when a field is removed or its meaning changes.
New fields may appear in any release, so consume it permissively.

Diagnostics and errors go to stderr, so stdout is always either valid JSON or
empty.

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-08T07:23:58.771035+00:00",
  "window_days": 30,
  "totals": {
    "estimated_cost_usd": 644.061,
    "input_tokens": 84051,
    "cache_write_tokens": 19728163,
    "cache_read_tokens": 508131522,
    "output_tokens": 1355236,
    "turns": 1425,
    "cache_hit_rate": 0.9624
  },
  "flagged_waste_usd": 31.2201,
  "flagged_waste_share": 0.0485,
  "by_model": { "claude-opus-5": 11.84 },
  "daily": { "2026-08-08": 11.84 },
  "sessions": [
    {
      "id": "a1aa79c9-d025-4e30-adc7-f450e646a1b7",
      "project": "/Users/me/src/app",
      "turns": 42,
      "estimated_cost_usd": 11.8402,
      "peak_context_tokens": 381446,
      "cache_hit_rate": 0.9701,
      "input_tokens": 1468,
      "cache_write_tokens": 367631,
      "cache_read_tokens": 12702852,
      "output_tokens": 63998,
      "started_at": "2026-08-08T06:11:02+00:00",
      "ended_at": "2026-08-08T07:19:44+00:00"
    }
  ],
  "findings": [
    {
      "kind": "cache_miss",
      "estimated_waste_usd": 0.8271,
      "scope": "bbbb2222",
      "project": "/Users/me/src/app",
      "detail": "10 turn(s) reprocessed 515,000 uncached input tokens…",
      "evidence": {
        "cold_turns": 10,
        "uncached_tokens": 515000,
        "model": "claude-opus-5"
      }
    }
  ]
}
```

## Field notes

| Field | Notes |
|---|---|
| `window_days` | The `--days` value the run used. |
| `sessions` | Sorted by cost descending, truncated to `--top`. |
| `findings` | Sorted by waste descending. **Not** truncated — the terminal report shows a subset, the JSON shows everything. |
| `scope` | A short session id, or a phrase like `"3 sessions"` for corpus-wide findings. |
| `project` | `"—"` when a finding is not attributable to one project. |
| `evidence` | Per-kind backing numbers. Keys vary by `kind`; see [`findings.md`](findings.md). Treat unknown keys as informational. |
| `*_usd` | Estimates at list prices, rounded to 6 decimal places. See the accuracy caveats in the README. |
| `flagged_waste_usd` | The sum of all findings. Findings overlap, so this can exceed `totals.estimated_cost_usd` (and `flagged_waste_share` can exceed `1.0`). |
| `detail` | Human-readable prose. Do not parse it — use `evidence` instead. |

## Privacy

`detail` and some `evidence` values contain excerpts of your own prompts, up to
110 characters each. Review a report before sharing it.

## Piping

```bash
# The five most expensive findings
proctor --json | jq -r '.findings[:5][] | "\(.estimated_waste_usd)\t\(.kind)"'

# Total cache-miss waste
proctor --json | jq '[.findings[] | select(.kind=="cache_miss")
                      | .estimated_waste_usd] | add'

# Sessions with a poor cache hit rate
proctor --json | jq '.sessions[] | select(.cache_hit_rate < 0.8) | .id'
```
