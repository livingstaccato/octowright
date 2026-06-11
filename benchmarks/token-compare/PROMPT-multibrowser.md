# Multi-browser coordination benchmark (Octowright's strength)

Tests driving THREE independent browsers at once — comparing one product across three
store sites, then ordering from the cheapest. This is where a multi-browser backend
(Octowright: one `instance_id` per browser) should pull ahead of a single-shared-browser
backend (Playwright MCP), which can only tab/serialize.

## How to run

Serve the fixtures (same server as the main benchmark):
```
cd benchmarks/token-compare && python3 -m http.server 8000
```
Stores: `store-a.html` (Lumen Supply Co.), `store-b.html` (BrightMart), `store-c.html` (DeskWorks).
Connect exactly one backend, fresh session, then paste the prompt below verbatim.

## The prompt (paste verbatim)

```
You have browser automation tools available. Use only those tools — don't guess page
contents, observe them through the tools. Use the browser tools directly and do NOT
invoke any Skill. Work step by step.

You will compare the SAME product across three independent store websites. Open EACH
store in its OWN separate browser and keep all three browsers open at the same time.

Stores:
- Store A: http://localhost:8000/store-a.html
- Store B: http://localhost:8000/store-b.html
- Store C: http://localhost:8000/store-c.html

Task:
1. Open all three stores, each in its own separate browser (three browsers open at once).
2. From each store, read the store name and the price of the Aurora Desk Lamp.
3. Identify which store has the LOWEST price.
4. In that store's browser, set the quantity to 3 and place the order, then read the
   confirmation message the page shows.
5. Without closing the other two browsers, switch back to the FIRST store you opened and
   re-read its price to confirm it is unchanged from step 2.

Then reply with:
A) A table — store name | price — for the three stores.
B) The cheapest store and the exact confirmation message text.
C) Whether all three browsers were open simultaneously (yes/no) and how you verified it
   (e.g., by listing the open browser instances).
D) A self-report (<=150 words): how many tool calls and how many browsers you used,
   whether coordinating three browsers at once was easy or hard, anything you retried or
   found ambiguous, and your confidence (low/med/high) that A-C are correct.

Do not skip part D.
```

## Ground truth (for scoring)

| Store | Name | Price |
|---|---|---|
| store-a | Lumen Supply Co. | $44.00 |
| store-b | BrightMart | $39.50 |
| store-c | DeskWorks | $41.00 |

- Cheapest: **BrightMart ($39.50)** — store-b
- Confirmation (store-b, qty 3): `Order placed: 3 × Aurora Desk Lamp`
- Step-5 re-check: the first store opened still shows its original price.

## What to capture (per backend)

Same as the main benchmark, plus multi-browser specifics:
- total tokens / tool calls / wall-clock (harness)
- **# distinct browser instances actually opened** (Octowright: count `instance_id`s;
  Playwright: did it use one shared browser + tabs, or fail to parallelize?)
- correctness: table / cheapest / confirmation / 3-open-at-once / re-check
- Did the backend hold three browsers concurrently, or serialize through one?
