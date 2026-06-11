# Token-compare: Playwright MCP vs Octowright

A token-usage and capability benchmark comparing two browser-automation MCP backends —
**Playwright MCP** (Microsoft's official browser-automation MCP) and **Octowright**
(provide-io's multi-browser MCP) — driven by the same Claude model on identical,
locally-served fixtures. The task prompts name no backend-specific tool; the model discovers
and uses whatever browser tools the connected backend exposes, so the same prompt runs
unchanged against either side.

**Full method, raw numbers, and analysis live in [`RESULTS.md`](./RESULTS.md).** This README
is the index: what each file is, how to reproduce a run, and how trials are measured.

## What's compared

| Benchmark | Prompt | Fixture(s) | RESULTS section | Measures |
|---|---|---|---|---|
| Single-page token cost | `PROMPT.md` | `catalog.html` | Conditions A–D · Token decomposition · Diff | Whole-conversation tokens on a fixed open→read→collapse→order→re-check flow |
| Multi-browser coordination | `PROMPT-multibrowser.md` | `store-a/b/c.html` | Multi-browser coordination | Can the backend hold 3 *independent* browsers open at once? |
| Multi-persona isolation | `PROMPT-isolation.md` | `whoami.html` | Multi-persona isolation | Do 3 sessions stay isolated, or does a shared context bleed? |
| Macro reuse | _(manual; see RESULTS)_ | `store-b.html` | Macro reuse | Record-once / replay-cheap token amortization (Octowright only) |

Each `PROMPT-*.md` is self-contained: it holds the verbatim prompt, the ground-truth table
for scoring, and what to capture.

## How to reproduce a run

1. **Serve the fixtures** (deterministic → no network/site variance), from this directory:
   ```
   python3 -m http.server 8000
   ```
   Fixtures are then at `http://localhost:8000/<file>.html`.
2. **Connect exactly one backend** — Playwright MCP *or* Octowright, **never both** in the
   same session (the model must not see both tool sets). Record the backend **version**
   (see "Backends under test" below).
3. **Fresh session per trial.** Paste the prompt from the relevant `PROMPT-*.md` verbatim.
4. **≥3 trials** per (model × backend × condition); average. Single runs are noisy.

### Concurrency rule (important)
- **Octowright** gives each subagent its own browser `instance_id` → **parallel-safe**.
- **Playwright MCP** drives **one shared browser** → parallel subagents collide
  (navigate/close stomp each other → `about:blank` resets). **Run Playwright trials
  sequentially**, one subagent at a time.
- This does not bias tokens (both are collision-free when run correctly), but it makes
  Octowright's wall-clock CPU-contended — **compare tokens, not seconds.**

### "Skill-off" (for a symmetric comparison)
Subagents auto-invoke the `octowright` skill unless told not to. For an apples-to-apples
run, append `do NOT invoke any Skill` to the prompt, applied **identically to both backends**.
Playwright has no equivalent skill anyway.

## Harness (how trials are measured)

Trials were run as one fresh `general-purpose` subagent each, given the verbatim prompt only:

- Each subagent writes a log at `…/<session>/subagents/agent-<id>.jsonl`.
- Parse that log for: total tokens, tool-call count (observe vs act split), per-tool-result
  char length, assistant turns, and output tokens.
- The harness `subagent_tokens` value ≈ final-turn footprint (in + cache + out); cross-check
  against the log.
- Score the final message against the `PROMPT-*.md` ground truth (table / cheapest /
  confirmation / re-check), pass/fail each.

> **Open task:** the harness is currently a manual procedure, not a script. Codifying the
> subagent spawn + `agent-*.jsonl` parser into a runnable tool is the main remaining
> reproducibility deliverable.

## Backends under test — record per run

Versions are **not** auto-captured. Pin them for every run so numbers stay reproducible:

| Field | Value |
|---|---|
| Model | `claude-opus-4-8` |
| Octowright version (commit / tag) | _record at run time_ |
| Playwright MCP version | _record at run time_ |
| Run date | _record at run time_ |

## Files

- `PROMPT.md`, `PROMPT-multibrowser.md`, `PROMPT-isolation.md` — task specs + ground truth.
- `catalog.html` — 5-product catalog (single-page benchmark).
- `store-a.html` / `store-b.html` / `store-c.html` — same product, three prices (multi-browser).
- `whoami.html` — localStorage-backed login (isolation test).
- `RESULTS.md` — method, raw per-trial numbers, decomposition, and findings.

## Headline findings (see `RESULTS.md` for the full picture)

- **Single-page tokens:** Octowright ~24.9k vs Playwright ~20.7k (skill-off, n=3) →
  Octowright **+20%**. The gap is **tool-surface size + per-backend turn/output overhead**,
  *not* page-state verbosity (Octowright's per-observation payloads are ~half the size) and
  *not* read strategy (a strategy-controlled twin stayed flat). Note part of the tool-surface
  component is a tunable packaging choice — see the caveat in `RESULTS.md`.
- **Multi-browser & isolation:** Octowright runs 3 genuinely isolated browsers; Playwright MCP
  can only open tabs in one shared context → multi-persona sessions **bleed** (all read the
  last login). This is a **correctness** gap, not a perf gap.
- **Macro reuse:** Octowright records → `macro_run` replays server-side in ~1 tool call
  (≈free on repeat); Playwright must re-drive via the LLM every run (≈linear token cost).
