# Token-compare results

Method and raw numbers for the Playwright-MCP vs Octowright token benchmark
(see `PROMPT.md`). Tokens = whole-conversation footprint incl. tool-result tokens
(harness `subagent_tokens` ≈ final-turn in+cache+out; cross-checked against logs).
Model: `claude-opus-4-8`. Fixture: `catalog.html` @ `http://localhost:8000`.

> **TODO — backend versions not captured.** These runs do not record the Octowright
> commit/tag or the Playwright MCP version. Pin both before citing these numbers as
> reproducible (see `README.md` → "Backends under test").

## Harness

- Each trial = one fresh `general-purpose` subagent, verbatim prompt only, own
  `…/subagents/agent-<id>.jsonl` log (parsed for metrics); final message scored vs
  ground truth (table / cheapest / confirmation / re-check).
- **Isolation matters:** Octowright gives each subagent its own browser `instance_id`
  (parallel-safe). The official Playwright MCP drives ONE shared browser, so parallel
  subagents collide (navigate/close stomp each other → `about:blank` resets). Playwright
  trials therefore MUST run **sequentially** (one subagent at a time). Octowright trials
  were parallel (isolated); Playwright trials sequential. This does not bias tokens
  (both collision-free); it does make Octowright's wall-clock CPU-contended, so the
  time column is NOT apples-to-apples — compare tokens, not seconds.
- **Skill:** the `octowright` browser skill exists; subagents auto-invoke it unless told
  not to. "skill-off" runs add `do NOT invoke any Skill` to the prompt (applied
  identically to both backends). Playwright has no equivalent skill anyway.
- Fixture bug fixed first: `<div id="confirm">` collided with builtin `window.confirm`
  so the order confirmation never rendered; renamed id → `msg`. All runs below are on
  the fixed fixture.

## Conditions

| id | backend | skill | concurrency | trials | status |
|----|---------|-------|-------------|--------|--------|
| A | octowright | allowed (realistic) | parallel-isolated | 3 | done |
| B | playwright-mcp | OFF (read via snapshot) | sequential-isolated | 3 | done |
| C | octowright | OFF | parallel-isolated | 3 | done |
| D | playwright-mcp | OFF (read via evaluate) | sequential-isolated | 3 | done |

The clean symmetric backend diff is **C vs B** (both skill-off). A is kept as a
"realistic Octowright" reference. **D is the strategy-controlled twin of B** — same
backend, but forced to read page state via `browser_evaluate` instead of snapshots, to
test whether read strategy (not backend) drives the token gap. A discarded Playwright
parallel run (collisions, 30–36k tokens / 33–43 calls) is not reported except as the
reason sequential is required.

## A — Octowright, skill-allowed, parallel (reference)

| trial | tokens | wall-clock | tools | obs/act | skill | correct? |
|---|---|---|---|---|---|---|
| 1 | 25,581 | 110 s | 25 | 10/10 | 0 | ✅ all 4 |
| 2 | 31,325 | 127 s | 25 | 9/10 | 1 | ✅ all 4 |
| 3 | 29,043 | 145 s | 29 | 14/10 | 1 | ✅ all 4 |
| **avg** | **28,650** | **127 s** | **26.3** | 11/10 | — | **100%** |

(Trial 1 happened to skip the skill: 25,581 tokens — a single skill-off Octowright
data point, used only as a rough hint below.)

## B — Playwright-MCP, skill-off, sequential (clean)

| trial | tokens | wall-clock | tools | obs/act | skill | correct? |
|---|---|---|---|---|---|---|
| 1 | 21,270 | 98 s | 20 | 9/9 | 0 | ✅ all 4 |
| 2 | 21,033 | 96 s | 20 | 9/9 | 0 | ✅ all 4 |
| 3 | 19,733 | 89 s | 18 | 7/9 | 0 | ✅ all 4 |
| **avg** | **20,679** | **94.6 s** | **19.3** | 8.3/9 | — | **100%** |

Per trial: 1 navigate + ~8 clicks + 7–9 snapshots + 1 fill_form + 1 ToolSearch. Very
low variance. Each `browser_click` returns a snapshot-file ref (not inline), so the
model takes an explicit `browser_snapshot` to read state — that's the observe driver.

## D — Playwright-MCP, skill-off, evaluate-driven (strategy-controlled twin of B)

Same backend/skill/concurrency as B; the prompt forced reading state via `browser_evaluate`
(targeted JS) instead of snapshots.

| trial | tokens | wall-clock | tools | correct? |
|---|---|---|---|---|
| 1 | 21,421 | 118 s | 20 | ✅ all 4 |
| 2 | 21,738 | 129 s | 22 | ✅ all 4 |
| 3 | 21,054 | 125 s | 23 | ✅ all 4 |
| **avg** | **21,404** | **124 s** | 21.7 | **100%** |

baseline ctx 10,017 · turns 36–37 · sum_output ~3,649 · tool-result chars/trial ~10,594
(`browser_evaluate` ×~11: mean 774 chars, but one up-front DOM dump hits ~3,295).

**D ≈ B (21,404 vs 20,679 — flat, D slightly higher).** Read strategy is ~token-neutral
on Playwright: evaluate cuts tool-result chars (~10.6k vs ~12.4k/trial) but the model pays
it back in **output tokens** (~3,649 vs ~2,100) authoring JS, plus more turns. The cost
just moves from the read side to the write side. **Refutes** the earlier guess that an
evaluate strategy would materially cut Playwright's tokens — and shows the A-vs-B backend
gap is NOT explained by read strategy.

## C — Octowright, skill-off, neutral strategy (clean, comparable to B)

| trial | tokens | wall-clock | tools | obs/act | strategy | correct? |
|---|---|---|---|---|---|---|
| 1 | 22,570 | 106 s | 21 | 9/10 | snapshot | ✅ all 4 |
| 2 | 25,723 | 197 s | 28 | 13/12 | evaluate (+timeout retry) | ✅ all 4 |
| 3 | 26,400 | 132 s | 27 | 12/10 | evaluate | ✅ all 4 |
| **avg** | **24,898** | **145 s** | **25.3** | ~11/11 | mixed | **100%** |

All Octowright, `SKILL:0` confirmed, baseline 11,330. Like Playwright, the model picked
its own read strategy (1 trial snapshot, 2 evaluate) — consistent with strategy being
~neutral. C2 hit transient Octowright transport timeouts (retried, still correct).

## Token decomposition & per-observation cost — the REAL driver

This is the most fair, model-independent view: it separates fixed tool-surface cost,
per-observation payload size, and the model's interaction strategy. It **inverts
PROMPT.md's assumption** that snapshot/accessibility verbosity dominates.

### Baseline context (system + tool surface + first user msg) — fixed per backend (~±150 tok)
| backend | baseline ctx tokens |
|---|---|
| octowright | **11,171** (range 11,171–11,330 across trials) |
| playwright-mcp | **9,913** (range 9,913–10,017 across trials) |

Octowright exposes ~111 (deferred) tools to the subagent vs ~22 for Playwright; that
larger surface costs **~1,258 tokens** before any page work. (Deferred = names listed,
schemas loaded on demand, so it's surface *names*, not full schemas.)

> **Caveat — "deterministic" means fixed within ~±150 tok, not bit-identical.** The baseline
> varies slightly by measurement point: Playwright reads 9,913 here but 10,017 in condition D;
> Octowright reads 11,171 here but 11,330 in condition C. The spread (tokenizer rounding + exactly
> where the first user message is counted) is small and does not affect the conclusions — but read
> these as **~10.0k (PW) vs ~11.2k (Octo)**, not as a single exact constant.
>
> **Caveat — the tool surface is a tunable packaging choice, not an intrinsic cost.** ~111 exposed
> tools is how *this* Octowright build is configured, not a law of multi-browser automation. A
> slimmer Octowright tool profile would shrink this ~1.25k baseline delta — i.e. **~30% of the
> +4,219 C-vs-B gap is attributable to tool-surface packaging that is configurable**, not to
> per-action overhead. So "structural gap" should be read as "structural *given the current tool
> profiles*." A reduced-surface Octowright re-run is the cleanest way to separate packaging cost
> from intrinsic cost — not yet done.

### Turns + output tokens (the interaction-strategy tax)
| backend (skill-off) | assistant turns | sum output tokens |
|---|---|---|
| octowright (a9bdd2) | 41 | 4,133 |
| playwright (3 trials) | 29–34 | 1,890–2,357 |

Octowright's model drives state reads with `browser_evaluate` (authoring JS each time)
→ ~2× the output tokens across more turns. Playwright snapshots-and-reads → leaner output.

### Per-observation payload size (chars) — Octowright is SMALLER
| tool | octowright | playwright-mcp |
|---|---|---|
| `browser_snapshot` | ~630 | **~1,190** (≈1.9×) |
| action echo (`click`) | **16** | 256 |
| `browser_evaluate` | ~340 | (model didn't use it) |
| **total tool-result chars / trial** | **~4,400** | **~12,400** |

### Net
Octowright's page-state reports are ~half the size of Playwright's AND its action echoes
are ~16× smaller — yet Octowright's **total** footprint is higher (~25.6k vs ~20.7k).
The gap is **structural, not read-strategy**:
- **Tool surface:** Octowright baseline 11,171 vs Playwright ~9,950 → ~+1.2k fixed, before
  any page work (111 deferred tools vs ~22).
- **Turns/output:** Octowright ran 41 turns / 4,133 output vs Playwright ~31 / ~2,100.
- **Read strategy is NOT it.** Condition D forced Playwright onto `browser_evaluate` (matching
  Octowright's approach) and it stayed flat at ~21.4k — so matching strategy did **not** close
  the gap. Strategy just shifts cost between tool-result chars and output tokens (net-neutral).

**Bottom line:** for this task the token gap is driven by **tool-surface size + per-backend
turn/output overhead**, not by page-state verbosity (Octowright is actually leaner there) and
not by read strategy. `PROMPT.md`'s "snapshot verbosity dominates" assumption does not hold here.
(The tool-surface component is configurable — see the baseline caveats above — so ~30% of the gap
reflects Octowright's current tool packaging, which a slimmer profile could reduce, rather than
intrinsic per-action cost.)

## Multi-browser coordination (Octowright's differentiator)

Separate test — fixtures `store-a/b/c.html` (3 stores, same product, different prices),
task = open all three in separate browsers, compare price, order from the cheapest.
See `PROMPT-multibrowser.md`. This measures a CAPABILITY, not just tokens.

### Playwright-MCP baseline (1 trial, skill-off)
- 17,944 tokens / 15 tools / 73 s. Data all correct (prices, cheapest = BrightMart $39.50,
  confirmation `Order placed: 3 × Aurora Desk Lamp`, re-check unchanged).
- **Capability wall: CANNOT open 3 separate browsers.** The official Playwright MCP drives
  ONE browser and exposes no tool to spawn independent browser processes; the model used
  **3 tabs** (`browser_tabs`) in a single shared context. It verified 3 tabs open at once,
  but they share cookies/session/storage/process — NOT isolated browsers. Subagent quote:
  "no tool to launch independent browser processes was exposed."

### Octowright (3 trials, skill-off)
| trial | tokens | tools | wall | browsers | 3 isolated & concurrent? | correct? |
|---|---|---|---|---|---|---|
| 1 | 24,104 | 17 | 62 s | 3 instances | ✅ `browser_list` count 3 | ✅ all |
| 2 | 23,795 | 16 | 65 s | 3 instances | ✅ | ✅ all |
| 3 | 22,674 | 15 | 60 s | 3 instances | ✅ | ✅ all |
| **avg** | **23,524** | 16 | 62 s | **3 separate browsers** | **✅** | **100%** |

Every trial opened 3 genuinely independent chromium instances (distinct `instance_id` +
own persistent profile `store-a/b/c`), via one batched parallel triple-launch, and verified
all three live at once with `browser_list` (count 3) — including after the order and the
Store-A re-read. True per-browser isolation, not tabs.

### Verdict — the capability gap, not the token gap, is the story
Octowright multi-browser ~23,524 tok vs Playwright ~17,944 → Octowright costs ~+5,580
(~+31%) more, because it launches/coordinates three real browser processes (Playwright just
adds tabs). BUT Playwright **cannot meet the requirement at all**: its 3 tabs share one
cookie/storage/process context. For any task needing session isolation (multi-account,
multi-persona, parallel independent logins) Playwright's approach fails outright — so the
premium buys a capability, it isn't waste. For tasks where 3 pages in a shared context are
fine, Playwright's tabs are cheaper. Match the backend to the isolation requirement.

## Multi-persona isolation — proves shared-context tabs fail

Fixture `whoami.html` (login stored in `localStorage['acme_user']`); test in
`PROMPT-isolation.md`. Three sessions log in as Alice, Bob, Carol (in that order), then
each re-reads "Who am I?". Isolation holds only if each session still shows its own user.

### Octowright — 3 separate browsers (empirical PASS)
| session | intended | "Who am I?" now reads |
|---|---|---|
| 1 | Alice | Logged in as: **Alice** ✅ |
| 2 | Bob | Logged in as: **Bob** ✅ |
| 3 | Carol | Logged in as: **Carol** ✅ |

Isolated = **YES**. Each browser ran its own profile/user-data-dir → separate cookie jar +
localStorage; confirmed at the storage layer (`localStorage['acme_user']` held its own value
in each). 23,880 tokens / 25 tools. (One transient Octowright transport drop mid-run, retried.)

### Shared context (Playwright MCP tabs) — CONFIRMED LIVE (fails)
Ran on Playwright-MCP: it cannot spawn independent browsers, so 3 **tabs in one context**.
All tabs share `localStorage['acme_user']`; logging in Alice→Bob→Carol overwrote the single
slot (last write wins). Every tab's "Who am I?" read:
| session | intended | "Who am I?" now reads |
|---|---|---|
| 1 | Alice | Logged in as: **Carol** ❌ |
| 2 | Bob | Logged in as: **Carol** ❌ |
| 3 | Carol | Logged in as: Carol |

Isolated = **NO**. Storage-verified (`acme_user="Carol"`, no cookies). The bleed appeared even
earlier — tab 2 showed "Logged in as: Alice" before it logged in. The username *fields* kept
Alice/Bob/Carol, but the live identity collapsed to one. 20,655 tokens / 24 tools, 1 subagent.
Confirms the multi-browser finding: Playwright offers only shared-context tabs.

### Takeaway
For multi-persona / multi-account work this is a **correctness gap, not a perf gap**:
Octowright's per-instance isolation is correct; Playwright's shared-context tabs silently
corrupt session state.

## Macro reuse (record once, replay cheap) — Octowright

Octowright records every action; `macro_save` → a reusable parameterized macro; `macro_run`
replays it **server-side in ONE tool call**, no LLM driving the steps. Playwright MCP has no
agent-side macro — repeating a task means re-driving via the LLM every run.

Demonstrated (store-b order flow, qty parameterized):
- Recorded `navigate → fill #qty → click "Place order"` on BrightMart; `macro_save` as
  `order_brightmart` with `qty` → `{{qty}}`.
- Replayed on a **fresh** browser via one `macro_run(args={qty:7})` → `executed: 3, elapsed
  0.062s`; page showed `Order placed: 7 × Aurora Desk Lamp` — live re-execution with the NEW
  arg (not a cached replay of the recorded `3`).

Token amortization (using the measured single-run costs above):
| run | Octowright (macro) | Playwright-MCP (re-drive) |
|---|---|---|
| 1 (record/drive) | ~one task's LLM cost (~25k) | ~20k LLM |
| 2…N (repeat) | **~1 tool call (~hundreds of tok)** | ~20k LLM **each** |
| 10 runs | ~25k | ~200k |

Repeats are ~free on Octowright, linear on Playwright. **Caveat:** macros amortize the
*deterministic* flow; a task needing fresh per-run judgment still pays the LLM for that part.

Two Octowright bugs surfaced while running this (filed in the octowright repo HANDOFF):
- `macro_save` retained recorder noise (`user_navigation` to `:6286/new-tab` + `markdown_cached`)
  that inflated replay past the **bridge request-timeout** (original `macro_run` timed out twice,
  half-applied); a hand-stripped 3-step macro ran in **62 ms**.
- The macro **progress-pill overlay** (`<span data-role="label">…click_by text=Place order…</span>`)
  collides with `get_by_text()` text locators → strict-mode violation. Use role/selector locators,
  or keep the overlay out of locator resolution.

## Diff

- **Clean (C vs B), both skill-off, n=3 each — single-page catalog task:**
  Octowright **24,898** vs Playwright **20,679** → Octowright **+4,219 tokens (+20.4%)**,
  +6.0 tool calls. Per the decomposition, the gap is tool-surface (+~1.3k baseline) +
  per-backend turn/output overhead — NOT read strategy, NOT report verbosity (Octowright's
  per-observation payloads are actually ~half the size).
- **Skill cost on Octowright (A vs C):** skill-on 28,650 vs skill-off 24,898 → the
  `octowright` skill adds ~**3,750 tokens (~+15%)** when the model invokes it.
- **Playwright read-strategy (B snapshot 20,679 vs D evaluate 21,404):** ~flat (+3.5%).
  Strategy is token-neutral here; it trades tool-result chars for output tokens.
- Correctness: 100% for every condition/trial (B, D, A all 4/4); confidence high throughout.
