#!/usr/bin/env python3
"""
proctor — audits your Claude Code / API usage for waste.

Reads Claude Code session transcripts (~/.claude/projects/**/*.jsonl) and/or the
Anthropic Admin API, then reports where tokens are being burned: cold context
that should have been cached, ballooning sessions, repeated context pasted
across sessions, oversized prompts, subagent burn, and premium models doing
light work. Every finding carries a dollar estimate.

Usage:
    proctor.py                        # audit ~/.claude/projects, last 30 days
    proctor.py --days 7               # last week only
    proctor.py /path/to/logs          # custom log dir(s)
    proctor.py --html report.html     # also write an HTML dashboard
    proctor.py --admin                # org-level report via Admin API
                                      # (needs ANTHROPIC_ADMIN_KEY, sk-ant-admin-*)
    proctor.py --json                 # machine-readable output
    proctor.py --top 15               # rows per table (default 10)

No dependencies — stdlib only. Prices as of Aug 2026; edit PRICING to update.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Pricing ($ per million tokens). Matched by substring against the model id.
# Order matters: first match wins. cw5/cw1 = 5-min / 1-h cache writes,
# cr = cache read. Source: platform.claude.com/docs/en/about-claude/pricing
# --------------------------------------------------------------------------
PRICING = [
    ("fable",      dict(inp=10.0, cw5=12.50, cw1=20.0, cr=1.00, out=50.0)),
    ("mythos",     dict(inp=10.0, cw5=12.50, cw1=20.0, cr=1.00, out=50.0)),
    ("opus-4-1",   dict(inp=15.0, cw5=18.75, cw1=30.0, cr=1.50, out=75.0)),
    ("opus-4-2025",dict(inp=15.0, cw5=18.75, cw1=30.0, cr=1.50, out=75.0)),
    ("opus",       dict(inp=5.0,  cw5=6.25,  cw1=10.0, cr=0.50, out=25.0)),
    ("sonnet-5",   dict(inp=2.0,  cw5=2.50,  cw1=4.0,  cr=0.20, out=10.0)),  # intro until 2026-09-01
    ("sonnet",     dict(inp=3.0,  cw5=3.75,  cw1=6.0,  cr=0.30, out=15.0)),
    ("haiku-3-5",  dict(inp=0.8,  cw5=1.00,  cw1=1.6,  cr=0.08, out=4.0)),
    ("haiku",      dict(inp=1.0,  cw5=1.25,  cw1=2.0,  cr=0.10, out=5.0)),
]
DEFAULT_PRICE = dict(inp=3.0, cw5=3.75, cw1=6.0, cr=0.30, out=15.0)
SONNET5_STD = dict(inp=3.0, cw5=3.75, cw1=6.0, cr=0.30, out=15.0)
SONNET5_CUTOVER = datetime(2026, 9, 1, tzinfo=timezone.utc)
CHEAP_ALTERNATIVE = "sonnet"  # what "model mismatch" savings are computed against

M = 1_000_000.0


def price_for(model, ts=None):
    m = (model or "").lower()
    for pat, p in PRICING:
        if pat in m:
            if pat == "sonnet-5" and ts and ts >= SONNET5_CUTOVER:
                return SONNET5_STD
            return p
    return DEFAULT_PRICE


def turn_cost(t):
    p = price_for(t["model"], t["ts"])
    return (t["inp"] * p["inp"] + t["cw5"] * p["cw5"] + t["cw1"] * p["cw1"]
            + t["cr"] * p["cr"] + t["out"] * p["out"]) / M


def est_tokens(text):
    return max(1, len(text) // 4)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Parsing Claude Code JSONL transcripts
# --------------------------------------------------------------------------

def iter_text_blocks(content):
    """Yield text strings from a message content field (str or block list)."""
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                yield block["text"]
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, str):
                    yield inner
                elif isinstance(inner, list):
                    for b in inner:
                        if isinstance(b, dict) and isinstance(b.get("text"), str):
                            yield b["text"]


def load_sessions(log_dirs, since):
    """Parse all *.jsonl transcripts into per-session records."""
    sessions = {}
    block_seen = defaultdict(set)   # hash -> set of session ids
    block_info = {}                 # hash -> (est_tokens, preview)

    files = []
    for d in log_dirs:
        d = Path(d).expanduser()
        if d.is_file():
            files.append(d)
        else:
            files.extend(d.rglob("*.jsonl"))

    for f in sorted(files):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < since:
                continue
        except OSError:
            continue
        project = f.parent.name.lstrip("-").replace("-", "/") or str(f.parent)
        sid_default = f.stem
        seen_msg_ids = {}
        with open(f, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                rtype = rec.get("type")
                msg = rec.get("message") or {}
                sid = rec.get("sessionId") or sid_default
                ts = parse_ts(rec.get("timestamp"))
                if ts and ts < since:
                    continue
                s = sessions.setdefault(sid, dict(
                    id=sid, project=project, file=str(f), turns=[],
                    user_prompts=[], first_ts=None, last_ts=None,
                    models=Counter()))
                if ts:
                    s["first_ts"] = min(filter(None, [s["first_ts"], ts]))
                    s["last_ts"] = max(filter(None, [s["last_ts"], ts]))

                if rtype == "user" and isinstance(msg.get("content"), (str, list)):
                    for text in iter_text_blocks(msg.get("content")):
                        # fingerprint paragraph-level chunks, so a pasted block
                        # is caught even when the text around it differs
                        for para in re.split(r"\n\s*\n", text):
                            if len(para) < 500:
                                continue
                            h = hashlib.sha1(
                                re.sub(r"\s+", " ", para).strip().encode()).hexdigest()
                            block_seen[h].add(sid)
                            block_info.setdefault(
                                h, (est_tokens(para),
                                    para.strip().replace("\n", " ")[:110]))
                    # genuine typed prompt (not tool_result)
                    if isinstance(msg.get("content"), str) and not rec.get("isMeta"):
                        s["user_prompts"].append(
                            (est_tokens(msg["content"]),
                             msg["content"].strip().replace("\n", " ")[:110]))
                    elif isinstance(msg.get("content"), list):
                        for block in msg["content"]:
                            if isinstance(block, dict) and block.get("type") == "text" \
                                    and not rec.get("isMeta"):
                                s["user_prompts"].append(
                                    (est_tokens(block.get("text", "")),
                                     block.get("text", "").strip().replace("\n", " ")[:110]))

                if rtype != "assistant":
                    continue
                usage = msg.get("usage")
                model = msg.get("model") or ""
                if not usage or model == "<synthetic>":
                    continue
                cc = usage.get("cache_creation") or {}
                cw5 = cc.get("ephemeral_5m_input_tokens")
                cw1 = cc.get("ephemeral_1h_input_tokens", 0)
                if cw5 is None:
                    cw5, cw1 = usage.get("cache_creation_input_tokens", 0) or 0, 0
                turn = dict(
                    ts=ts, model=model,
                    inp=usage.get("input_tokens", 0) or 0,
                    cw5=cw5 or 0, cw1=cw1 or 0,
                    cr=usage.get("cache_read_input_tokens", 0) or 0,
                    out=usage.get("output_tokens", 0) or 0,
                    sidechain=bool(rec.get("isSidechain")))
                # streaming/retries can emit the same message id repeatedly:
                # keep the biggest-output copy only
                mid = msg.get("id")
                key = (sid, mid)
                if mid and key in seen_msg_ids:
                    old = seen_msg_ids[key]
                    if turn["out"] >= old["out"]:
                        s["turns"][s["turns"].index(old)] = turn
                        seen_msg_ids[key] = turn
                    continue
                if mid:
                    seen_msg_ids[key] = turn
                s["turns"].append(turn)
                s["models"][model] += 1

    dupes = {h: sids for h, sids in block_seen.items() if len(sids) > 1}
    return sessions, dupes, block_info


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyze(sessions, dupes, block_info, top=10):
    report = dict(sessions=[], findings=[], daily=Counter(), by_model=Counter(),
                  totals=Counter(), top=top)
    findings = report["findings"]

    for s in sessions.values():
        turns = s["turns"]
        if not turns:
            continue
        cost = sum(turn_cost(t) for t in turns)
        inp = sum(t["inp"] for t in turns)
        cw = sum(t["cw5"] + t["cw1"] for t in turns)
        cr = sum(t["cr"] for t in turns)
        out = sum(t["out"] for t in turns)
        ctx_final = max(t["inp"] + t["cw5"] + t["cw1"] + t["cr"] for t in turns)
        side_cost = sum(turn_cost(t) for t in turns if t["sidechain"])
        denom = inp + cw + cr
        s.update(cost=cost, inp=inp, cw=cw, cr=cr, out=out, ctx_final=ctx_final,
                 side_cost=side_cost, cache_rate=(cr / denom if denom else 0.0),
                 n_turns=len(turns))
        report["sessions"].append(s)
        report["totals"].update(dict(cost=cost, inp=inp, cw=cw, cr=cr, out=out,
                                     turns=len(turns)))
        for t in turns:
            report["by_model"][t["model"]] += turn_cost(t)
            if t["ts"]:
                report["daily"][t["ts"].date().isoformat()] += turn_cost(t)

        # -- finding: cold context reprocessed (cache misses mid-session)
        cold = [t for t in turns[1:] if t["cr"] == 0 and t["inp"] > 5000]
        if cold:
            toks = sum(t["inp"] for t in cold)
            p = price_for(turns[0]["model"], turns[0]["ts"])
            waste = toks * (p["inp"] - p["cr"]) / M * 0.9  # allow for write premium
            if waste >= 0.01:
                findings.append(dict(
                    kind="cache_miss", waste=waste, session=s["id"][:8],
                    project=s["project"],
                    detail=f"{len(cold)} turn(s) reprocessed {toks:,} uncached input "
                           f"tokens mid-session. Cache reads cost 10% of base input — "
                           f"this context was paid for at full price again."))

        # -- finding: ballooned session (huge context, thin output)
        if ctx_final > 120_000 and out < ctx_final * 0.02 and len(turns) >= 10:
            tail = turns[len(turns) // 2:]
            tail_ctx_cost = sum(
                (t["inp"] * price_for(t["model"], t["ts"])["inp"]
                 + t["cr"] * price_for(t["model"], t["ts"])["cr"]
                 + (t["cw5"] + t["cw1"]) * price_for(t["model"], t["ts"])["cw5"]) / M
                for t in tail)
            findings.append(dict(
                kind="balloon", waste=tail_cost_share(tail_ctx_cost), session=s["id"][:8],
                project=s["project"],
                detail=f"Context grew to {ctx_final:,} tokens over {len(turns)} turns "
                       f"while producing only {out:,} output tokens "
                       f"({out / ctx_final:.1%}). The back half of the session spent "
                       f"~${tail_ctx_cost:.2f} just re-reading accumulated context. "
                       f"Compact or restart sessions before they snowball."))

        # -- finding: sidechain / subagent burn
        if cost > 0.5 and side_cost / cost > 0.4:
            findings.append(dict(
                kind="sidechain", waste=side_cost * 0.5, session=s["id"][:8],
                project=s["project"],
                detail=f"Subagents consumed ${side_cost:.2f} of this session's "
                       f"${cost:.2f} ({side_cost / cost:.0%}). Each subagent starts "
                       f"cold and re-derives context the parent already had."))

        # -- finding: premium model, light work
        prem = [t for t in turns if any(k in (t["model"] or "").lower()
                                        for k in ("opus", "fable", "mythos"))]
        if prem and cost > 0.25 and out < 2000:
            alt = next(p for pat, p in PRICING if pat == CHEAP_ALTERNATIVE)
            alt_cost = sum((t["inp"] * alt["inp"] + (t["cw5"] + t["cw1"]) * alt["cw5"]
                            + t["cr"] * alt["cr"] + t["out"] * alt["out"]) / M
                           for t in turns)
            if cost - alt_cost >= 0.05:
                findings.append(dict(
                    kind="model_mismatch", waste=cost - alt_cost, session=s["id"][:8],
                    project=s["project"],
                    detail=f"{prem[0]['model']} produced only {out:,} output tokens "
                           f"for ${cost:.2f}. On {CHEAP_ALTERNATIVE} this session "
                           f"would be ~${alt_cost:.2f}."))

    # -- finding: identical large blocks pasted into multiple sessions
    for h, sids in sorted(dupes.items(),
                          key=lambda kv: -block_info[kv[0]][0] * len(kv[1]))[:top]:
        toks, preview = block_info[h]
        n = len(sids)
        wasted_toks = toks * (n - 1)
        waste = wasted_toks * DEFAULT_PRICE["inp"] / M
        if waste < 0.01:
            continue
        findings.append(dict(
            kind="repeat", waste=waste, session=f"{n} sessions", project="—",
            detail=f"The same ~{toks:,}-token block appeared in {n} sessions "
                   f"(~{wasted_toks:,} redundant tokens). Put it in CLAUDE.md, a "
                   f"skill, or a file the agent reads on demand instead of "
                   f"re-pasting it. “{preview}…”"))

    # -- finding: oversized prompts
    all_prompts = [(tk, pv, s["id"][:8]) for s in report["sessions"]
                   for tk, pv in s["user_prompts"]]
    if len(all_prompts) >= 5:
        sizes = sorted(tk for tk, _, _ in all_prompts)
        median = sizes[len(sizes) // 2]
        thresh = max(median * 6, 3000)
        fat = sorted((p for p in all_prompts if p[0] > thresh), reverse=True)[:top]
        for tk, pv, sid in fat:
            findings.append(dict(
                kind="fat_prompt", waste=tk * DEFAULT_PRICE["inp"] / M,
                session=sid, project="—",
                detail=f"~{tk:,}-token prompt vs a median of ~{median:,}. That text "
                       f"rides along in context every turn afterward. "
                       f"“{pv}…”"))

    findings.sort(key=lambda f: -f["waste"])
    report["sessions"].sort(key=lambda s: -s["cost"])
    report["potential_savings"] = sum(f["waste"] for f in findings)
    return report


def tail_cost_share(x):
    return x * 0.6  # rough share of tail context cost attributable to bloat


# --------------------------------------------------------------------------
# Admin API (org-level)
# --------------------------------------------------------------------------

def admin_get(path, key, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://api.anthropic.com{path}?{q}"
    out = []
    while url:
        req = urllib.request.Request(url, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        out.extend(data.get("data", []))
        url = (f"https://api.anthropic.com{path}?{q}&page={data['next_page']}"
               if data.get("has_more") and data.get("next_page") else None)
    return out


def admin_report(days):
    key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
    if not key.startswith("sk-ant-admin"):
        sys.exit("--admin needs ANTHROPIC_ADMIN_KEY (an sk-ant-admin-* key).")
    start = (datetime.now(timezone.utc) - timedelta(days=days)) \
        .strftime("%Y-%m-%dT00:00:00Z")
    usage = admin_get("/v1/organizations/usage_report/messages", key,
                      {"starting_at": start, "bucket_width": "1d",
                       "group_by[]": "model", "limit": 31})
    cost = admin_get("/v1/organizations/cost_report", key,
                     {"starting_at": start, "limit": 31})

    print(f"\n=== Org usage, last {days} days (Admin API) ===")
    agg = defaultdict(Counter)
    for bucket in usage:
        for row in bucket.get("results", []):
            c = agg[row.get("model", "?")]
            c["inp"] += row.get("uncached_input_tokens", 0)
            c["cr"] += row.get("cache_read_input_tokens", 0)
            cc = row.get("cache_creation") or {}
            c["cw"] += (cc.get("ephemeral_5m_input_tokens", 0)
                        + cc.get("ephemeral_1h_input_tokens", 0)) \
                if cc else row.get("cache_creation_input_tokens", 0)
            c["out"] += row.get("output_tokens", 0)
    print(f"{'model':40} {'uncached in':>12} {'cache read':>12} "
          f"{'cache write':>12} {'output':>10} {'hit rate':>9}")
    for m, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
        denom = c["inp"] + c["cr"] + c["cw"]
        print(f"{m:40} {c['inp']:>12,} {c['cr']:>12,} {c['cw']:>12,} "
              f"{c['out']:>10,} {c['cr'] / denom if denom else 0:>8.0%}")
    total_cents = sum(float(r.get("amount", 0)) for b in cost
                      for r in b.get("results", []))
    print(f"\nOrg cost over period: ${total_cents / 100:,.2f}")
    for m, c in agg.items():
        denom = c["inp"] + c["cr"] + c["cw"]
        if denom > 1_000_000 and c["cr"] / denom < 0.5:
            p = price_for(m)
            save = c["inp"] * (p["inp"] - p["cr"]) / M * 0.7
            print(f"  ⚠ {m}: cache hit rate {c['cr'] / denom:.0%} — better caching "
                  f"could save roughly ${save:,.2f} over this period.")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

KIND_LABEL = dict(cache_miss="COLD CONTEXT", balloon="BALLOONED SESSION",
                  repeat="REPEATED CONTEXT", fat_prompt="OVERSIZED PROMPT",
                  sidechain="SUBAGENT BURN", model_mismatch="MODEL MISMATCH")


def render_terminal(r):
    t = r["totals"]
    denom = t["inp"] + t["cw"] + t["cr"]
    print(f"\n=== proctor: {len(r['sessions'])} sessions, "
          f"{t['turns']:,} API turns ===\n")
    print(f"  estimated spend      ${t['cost']:,.2f}")
    print(f"  input (uncached)     {t['inp']:>14,} tok")
    print(f"  cache writes         {t['cw']:>14,} tok")
    print(f"  cache reads          {t['cr']:>14,} tok   "
          f"(hit rate {t['cr'] / denom if denom else 0:.0%})")
    print(f"  output               {t['out']:>14,} tok")
    print(f"  flagged waste        ${r['potential_savings']:,.2f} "
          f"({r['potential_savings'] / t['cost'] if t['cost'] else 0:.0%} of spend)\n")

    if r["by_model"]:
        print("  spend by model:")
        for m, c in sorted(r["by_model"].items(), key=lambda kv: -kv[1]):
            print(f"    {m:44} ${c:>9,.2f}")
        print()

    print(f"--- top sessions by cost ---")
    print(f"  {'session':10} {'project':28} {'turns':>5} {'ctx max':>9} "
          f"{'hit':>5} {'cost':>9}")
    for s in r["sessions"][:r["top"]]:
        print(f"  {s['id'][:8]:10} {s['project'][-28:]:28} {s['n_turns']:>5} "
              f"{s['ctx_final']:>9,} {s['cache_rate']:>5.0%} ${s['cost']:>8,.2f}")

    print(f"\n--- findings (ranked by estimated waste) ---")
    if not r["findings"]:
        print("  Nothing flagged. Clean bill of health.")
    for f in r["findings"][:r["top"] * 2]:
        print(f"\n  [${f['waste']:,.2f}] {KIND_LABEL[f['kind']]}  "
              f"({f['session']}, {f['project']})")
        for line in wrap(f["detail"], 76):
            print(f"      {line}")
    print()


def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def render_html(r, path):
    t = r["totals"]
    denom = t["inp"] + t["cw"] + t["cr"]
    days = sorted(r["daily"].items())
    dmax = max((c for _, c in days), default=1)
    bars = "".join(
        f'<div class="bar" title="{d}: ${c:,.2f}">'
        f'<div class="fill" style="height:{max(2, c / dmax * 100):.0f}%"></div>'
        f'<span>{d[5:]}</span></div>' for d, c in days)
    rows = "".join(
        f"<tr><td>{s['id'][:8]}</td><td>{esc(s['project'][-40:])}</td>"
        f"<td>{s['n_turns']}</td><td>{s['ctx_final']:,}</td>"
        f"<td>{s['cache_rate']:.0%}</td><td>${s['cost']:,.2f}</td></tr>"
        for s in r["sessions"][:r["top"]])
    finds = "".join(
        f'<div class="find"><span class="tag {f["kind"]}">{KIND_LABEL[f["kind"]]}'
        f'</span><b>${f["waste"]:,.2f}</b> — {esc(f["detail"])} '
        f'<i>({f["session"]}, {esc(f["project"])})</i></div>'
        for f in r["findings"][:r["top"] * 2]) or "<p>Nothing flagged.</p>"
    models = "".join(
        f"<tr><td>{esc(m)}</td><td>${c:,.2f}</td></tr>"
        for m, c in sorted(r["by_model"].items(), key=lambda kv: -kv[1]))

    html = f"""<!doctype html><meta charset="utf-8"><title>proctor report</title>
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,sans-serif;margin:2rem auto;
      max-width:960px;padding:0 1rem;color:#1a1a1a;background:#fafaf7}}
 h1{{font-size:1.4rem}} h2{{font-size:1.05rem;margin-top:2rem}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap}}
 .card{{flex:1;min-width:140px;background:#fff;border:1px solid #e2e0da;
       border-radius:10px;padding:.9rem 1.1rem}}
 .card b{{display:block;font-size:1.5rem}} .card span{{color:#777;font-size:.8rem}}
 .chart{{display:flex;align-items:flex-end;gap:4px;height:140px;background:#fff;
        border:1px solid #e2e0da;border-radius:10px;padding:1rem}}
 .bar{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
      align-items:center;height:100%}}
 .bar .fill{{width:100%;background:#c96442;border-radius:3px 3px 0 0}}
 .bar span{{font-size:.6rem;color:#999;margin-top:4px}}
 table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e0da;
       border-radius:10px}}
 td,th{{padding:.45rem .7rem;border-bottom:1px solid #eee;text-align:left;
       font-variant-numeric:tabular-nums}}
 .find{{background:#fff;border:1px solid #e2e0da;border-radius:10px;
       padding:.7rem 1rem;margin:.5rem 0}}
 .tag{{font-size:.65rem;font-weight:700;letter-spacing:.05em;padding:.15rem .5rem;
      border-radius:99px;margin-right:.5rem;background:#eee}}
 .cache_miss{{background:#fde5d8}} .balloon{{background:#fdeecd}}
 .repeat{{background:#e3ecfb}} .fat_prompt{{background:#eee6fb}}
 .sidechain{{background:#e0f3e6}} .model_mismatch{{background:#fbe3e8}}
 i{{color:#999;font-size:.8rem}}
</style>
<h1>proctor — usage audit</h1>
<div class="cards">
 <div class="card"><b>${t['cost']:,.2f}</b><span>estimated spend</span></div>
 <div class="card"><b>{t['cr'] / denom if denom else 0:.0%}</b><span>cache hit rate</span></div>
 <div class="card"><b>${r['potential_savings']:,.2f}</b><span>flagged waste</span></div>
 <div class="card"><b>{len(r['sessions'])}</b><span>sessions / {t['turns']:,} turns</span></div>
</div>
<h2>Daily burn</h2><div class="chart">{bars or '<p>no data</p>'}</div>
<h2>Findings</h2>{finds}
<h2>Top sessions by cost</h2>
<table><tr><th>session</th><th>project</th><th>turns</th><th>max context</th>
<th>cache hit</th><th>cost</th></tr>{rows}</table>
<h2>Spend by model</h2><table>{models}</table>
<p><i>Costs estimated from per-turn usage at list prices (Aug 2026); actual
billing may differ. Generated {datetime.now():%Y-%m-%d %H:%M}.</i></p>"""
    Path(path).write_text(html)
    print(f"HTML report written to {path}")


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Audit Claude usage logs for waste.")
    ap.add_argument("logdirs", nargs="*",
                    default=[os.path.expanduser("~/.claude/projects")])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--html", metavar="FILE")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--admin", action="store_true",
                    help="org-level report via Admin API (ANTHROPIC_ADMIN_KEY)")
    args = ap.parse_args()

    if args.admin:
        admin_report(args.days)
        if not any(Path(os.path.expanduser(d)).exists() for d in args.logdirs):
            return

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    dirs = [d for d in args.logdirs if Path(os.path.expanduser(d)).exists()]
    if not dirs:
        sys.exit(f"No log directory found (looked in {args.logdirs}). "
                 f"Pass the path to your Claude Code logs explicitly.")
    sessions, dupes, block_info = load_sessions(dirs, since)
    if not any(s["turns"] for s in sessions.values()):
        sys.exit(f"No API turns found in the last {args.days} days under {dirs}.")
    report = analyze(sessions, dupes, block_info, top=args.top)

    if args.json:
        out = dict(totals=dict(report["totals"]),
                   potential_savings=report["potential_savings"],
                   by_model=dict(report["by_model"]),
                   daily=dict(report["daily"]),
                   sessions=[{k: s[k] for k in
                              ("id", "project", "n_turns", "cost", "ctx_final",
                               "cache_rate", "inp", "cw", "cr", "out")}
                             for s in report["sessions"]],
                   findings=report["findings"])
        print(json.dumps(out, indent=2, default=str))
    else:
        render_terminal(report)
    if args.html:
        render_html(report, args.html)


if __name__ == "__main__":
    main()
