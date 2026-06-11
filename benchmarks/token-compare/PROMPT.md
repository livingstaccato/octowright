# Token-usage benchmark: Playwright MCP vs Octowright

Backend-agnostic browser task for comparing **token usage** (and time / behavior)
across whichever browser MCP is toggled on. The prompt names no tool or feature of
either backend — the model discovers and uses whatever tools it has. Run the same
prompt against each backend (one at a time, never both) and diff the results.

## How to run

1. Serve the fixture (deterministic page → no network/site variance):
   ```
   cd benchmarks/token-compare && python3 -m http.server 8000
   ```
   Target URL: `http://localhost:8000/catalog.html`
2. Connect **exactly one** backend (Playwright MCP *or* Octowright — never both),
   in a **fresh session**, with the model settings you want to compare.
3. Paste the prompt below verbatim. Capture the metrics (next section).
4. Repeat for each `(model × backend)`, ≥3 trials each, and average.

## The prompt (paste verbatim)

```
You have browser automation tools available. Use only those tools to do the task —
don't guess page contents, observe them through the tools. Work step by step.

Target: http://localhost:8000/catalog.html

Task:
1. Go to the target page. It shows a product catalog.
2. For each of the first THREE products (top to bottom), one at a time:
   a. Open that product's details.
   b. Read its name, price, and star rating.
   c. Collapse it before moving to the next.
3. Identify the cheapest of those three products.
4. In the "Quick order" form, enter that cheapest product's exact name and a
   quantity of 2, then submit.
5. Read the confirmation message the page shows.
6. Re-open the FIRST product's details again and confirm its price still matches
   what you read in step 2.

Then reply with:
A) A table — product name | price | rating — for the three products.
B) The cheapest product, and the exact confirmation message text.
C) Whether the first product's price matched on re-check (yes/no).
D) A self-report (<=150 words): how many tool calls you used, whether reading the
   page state was easy or hard, anything you retried or found ambiguous, and your
   confidence (low/med/high) that A-C are correct.

Do not skip step 6 or part D.
```

The repetition (3× open → read → collapse, plus the re-open in step 6) is where the
backends diverge: each "observe" round-trip is the dominant token driver, measured
4 times per run.

## Ground truth (for scoring)

| Product | Price | Rating |
|---|---|---|
| Aurora Desk Lamp | $42.00 | 4.6 |
| Nimbus Water Bottle | $18.50 | 4.2 |
| Quartz Wall Clock | $35.00 | 4.8 |

- Cheapest of the first three: **Nimbus Water Bottle**
- Confirmation text: `Order placed: 2 × Nimbus Water Bottle`
- Step-6 re-check (first product price): `$42.00`

## Per-run capture

The model can't reliably count its own tokens — your harness provides those.

| Field | Source |
|---|---|
| model id; backend (`playwright-mcp` vs `octowright`) + version | you |
| total / input / output tokens | harness |
| wall-clock seconds | harness |
| tool-call count (and observe-vs-act split if visible) | harness |
| correct? (table / cheapest / confirmation / re-check — pass/fail each) | score vs ground truth |
| model self-report (part D) | paste |
| char length of each tool result (optional, high-signal) | harness/logs |

## Cross-run comparison

One row per run, grouped by model then backend:

`model | backend | trial | total tokens | wall-clock | tool calls | correct?`

Then per `(model, backend)`: **avg tokens**, **Δ tokens octowright−playwright (and %)**,
avg tool calls, avg time, correctness rate.

## Fairness / method

- **One backend per run** — the model must never see both tool sets.
- **Fresh session per run** (no carried context); same prompt text + same URL; same
  model settings (temperature / thinking budget) across backends for a given model.
- **≥3 trials per (model, backend)** and average — single runs are noisy.
- Keep the page **local/fixed**; a live site changes between runs and corrupts the diff.
- Count tokens the same way for both — whole-conversation total, **including tool-result
  tokens** (that's the whole point).
- The dominant driver is usually how each backend reports page state (snapshot/accessibility
  verbosity) and how many observe calls the model makes. Logging per-tool-result char length
  lets you attribute token cost to specific observations rather than just totals.
