# Findings reference

Every check proctor runs, what trips it, and exactly how its dollar figure is
computed. The thresholds and coefficients below are the constants at the top of
each detector module — if you disagree with one, that is where to change it.

## Reading the estimates

Two different kinds of number appear in a report:

**Measured.** Token counts on assistant turns come straight from the `usage`
object the API returned. These are exact.

**Estimated.** User prompts, pasted blocks, and tool results are sized at four
characters per token, because the API reports usage per *request*, not per
content block. Anything derived from these is approximate and labelled as such
below.

Every waste figure is deliberately conservative. Where a finding requires
guessing at a counterfactual — "what would this have cost if done right?" — a
fixed recovery factor is applied rather than assuming the ideal case. The result
is a ranked worklist, not an invoice adjustment.

**Findings overlap and are not additive.** One expensive session can trip several
checks that each blame the same tokens: a ballooned session is often also a
cold-context session, and a session flagged for model mismatch may be flagged for
subagent burn too. Total flagged waste can therefore exceed total spend. Use the
per-finding figures for ranking; do not read the total as an amount recoverable
by fixing everything.

---

## `cache_miss` — Cold context

**What it means.** A turn mid-session sent a large prompt with zero cache reads.
Prompt caching is a prefix match: any byte change in the prefix invalidates
everything after it, so this usually means the system prompt was edited, the tool
set changed, or more than five minutes elapsed and the cache TTL lapsed.

**Trips when** a turn other than the first has `cache_read == 0` and more than
**5,000** uncached input tokens.

**Estimate.**

```
waste = uncached_tokens × (input_price − cache_read_price) × 0.9
```

The `0.9` accounts for the fact that a perfectly cached alternative still pays a
cache write at 1.25× base input, so the full spread is not recoverable.

**Why the first turn is excluded.** Every session's opening turn is uncached by
definition. Flagging it would put a finding on every session in the corpus.

**When to ignore it.** One-shot sessions and sessions where you deliberately
changed the tool set mid-run will trip this legitimately.

---

## `balloon` — Ballooned session

**What it means.** Context accumulated to the point where most of what you are
paying for is the session re-reading its own history.

**Trips when** all three hold: peak context exceeds **120,000** tokens, the
session ran at least **10** turns, and total output is under **2%** of peak
context.

**Estimate.** The context cost (input + cache reads + cache writes, excluding
output) of the session's back half, multiplied by **0.6**.

```
waste = tail_context_cost × 0.6
```

The back half of a ballooned session is still doing real work, so only part of
what it spends carrying context is avoidable. 0.6 is a judgement call, held
constant so that findings are comparable between runs.

**What to do.** Compact or restart before a session snowballs.

---

## `repeat` — Repeated context

**What it means.** The same large block of text appeared verbatim in several
different sessions — the signature of re-pasting the same conventions, schema, or
spec into every new conversation.

**How it is detected.** User messages are split on blank lines and each paragraph
over **500 characters** is whitespace-normalized and SHA-1 fingerprinted.
Fingerprinting at paragraph granularity means a pasted block is still detected
when the text around it differs.

**Trips when** one fingerprint appears in two or more sessions.

**Estimate** *(approximate — token counts are character-derived)*:

```
waste = block_tokens × (sessions − 1) × default_input_price
```

The default Sonnet-tier input rate is used rather than a per-session rate,
because the block spans sessions that may have run on different models.

**What to do.** Move it into `CLAUDE.md`, a skill, or a file the agent reads on
demand.

---

## `redundant_tool` — Redundant tool calls

**What it means.** The agent issued the same tool call, with byte-identical
arguments, several times in one session — re-reading a file it already had, or
re-running a search it already ran.

**How it is detected.** Each `tool_use` block is keyed by a hash of the tool name
plus its input serialized with sorted keys, so argument ordering does not create
false distinctions. Results are matched back to their calls via `tool_use_id`.

**Trips when** one signature appears **3 or more** times *and* the result is at
least **400** tokens. Two identical calls is often a legitimate re-check; cheap
calls waste nothing worth reporting regardless of how often they run.

**Estimate** *(approximate)*:

```
waste = Σ result_tokens × (repeats − 1) × cache_write_5m_price
```

Summed across every repeated signature in the session; the finding text names the
worst offender.

---

## `fat_prompt` — Oversized prompt

**What it means.** A single prompt far larger than your own typical prompt. It is
not just the cost of sending it once — that text stays in context for every turn
that follows.

**Trips when** a prompt exceeds both **6× the corpus median** and **3,000
tokens**, and the corpus has at least **5** prompts to compute a median from.

Using your own median rather than a fixed size is deliberate: "large" depends
entirely on how you work.

**Estimate** *(approximate)*: `prompt_tokens × input_price`. This counts the
first send only and therefore understates the true carry cost.

---

## `sidechain` — Subagent burn

**What it means.** Sidechain (subagent) turns dominated the session's cost. Each
subagent starts with a cold context and re-derives things the parent already
knew.

**Trips when** the session cost more than **$0.50** and sidechain turns account
for more than **40%** of it.

**Estimate.**

```
waste = sidechain_cost × 0.5
```

Half, because subagents doing genuinely independent parallel work are worth their
cost while ones re-reading the same repo are not — and proctor cannot tell which
from the transcript alone.

**When to ignore it.** Wide fan-out work (many files, many independent tracks) is
exactly what subagents are for.

---

## `model_mismatch` — Model mismatch

**What it means.** A premium model (Opus, Fable, or Mythos) ran a session that
produced very little output.

**Trips when** a premium model was used, the session cost more than **$0.25**,
total output was under **2,000** tokens, and the counterfactual saves at least
**$0.05**.

**Estimate.** The session's actual cost minus what the same token volume would
have cost at Sonnet standard rates ($3/$15 per million).

**Read this one carefully.** Output volume is a proxy for task difficulty and a
poor one — a short answer to a hard question is exactly what a premium model is
for. This is a cost comparison, not a quality judgement, and model choice is
yours to make.

---

## Adding a check

See [`CONTRIBUTING.md`](../CONTRIBUTING.md). The short version: subclass
`SessionDetector` (or `Detector` for corpus-wide checks), register it in
`src/proctor/detectors/__init__.py`, and write both a test that trips it and a
test that proves it stays quiet on a healthy session. The second test is the one
that matters — an auditor that cries wolf gets muted.
