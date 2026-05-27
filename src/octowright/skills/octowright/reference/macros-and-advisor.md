---
name: macros-and-advisor
description: Reference for the Octowright Advisor pattern, macro observation recording, signature writing, and macro lifecycle management.
---

# Macros & Advisor Reference

## Advisor: Per-Session Bootstrap

Do this **once per conversation**, the first time Octowright comes up:

1. Call `octowright_status`.
2. Inspect the `advisor` block — note current preferences and any pending `suggestions`.
3. Surface any `macro_candidate` suggestion to the user **before** starting your task.

## Recording Macro Observations

### The Mechanism

`octowright_advisor_record_macro_observation(source="llm", signature, summary)` appends an observation. **Two observations with identical `signature` strings** produce a `macro_candidate` suggestion in the next status call.

The candidate is always prompt-only — even with `preference="automatic"`, macros are never auto-saved. Record proactively; surface the candidate; let the user decide.

### When to Record

- A multi-step workflow you're driving by hand (login, fill+submit, navigate-and-extract). Anything with 3+ browser tool calls in a deterministic sequence.
- A workflow that targets a persona/site combination you've seen before in this session.
- A workflow you'd have to retype selectors for under time pressure.

### When NOT to Record

- One-off exploration where the next steps depend on what you find.
- Single-action tasks (one click, one navigate).
- Anything driven by user input that varies per call — the `signature` must be stable.

### Writing a Good `signature`

- **Stable across runs.** No timestamps, UUIDs, search terms, or per-call inputs. Two calls an hour apart must produce the same string.
- **Identity-bearing.** Combine action shape with target: `"discord-login:dante"`, `"github-pr-comment:scenario-reviewer"`, `"canvas-claim-5-tiles:player-1"`.
- **Short.** Slug-style (`a-z0-9-:`), under 60 chars.

Bad: `"login"` (no target), `"discord"` (no action), `"login-2026-05-26"` (has date)

Good: `"discord-login:dante"`, `"github-open-pr:livingstaccato-octowright"`

### Writing a Good `summary`

One short prose line a human recognizes at a glance: `"Logged in as Dante on Discord via the standard email-then-2FA flow"`.

Don't include selectors or URLs with credentials — `_redact_summary` strips obvious patterns but isn't bulletproof.

## After the Candidate Appears

The next `octowright_status` or `octowright_advisor_status` call shows a `macro_candidate` suggestion under `advisor.suggestions`.

**Surface it to the user** — phrase as a question: *"I've done X twice this session — want me to save it as a macro named `<name>`?"*

- **Yes** → `macro_save(name, actions=[...])` building the action list from the workflow you just ran.
- **No** → `octowright_advisor_set_preference("macro_candidate", "no")` to stop suggesting.
- **"Do this automatically next time"** → set `"automatic"`. Even then, macros stay prompt-only — the agent still asks before saving.

## Macro Management

**Reuse:** Check `macro_list` before manually implementing a common flow (login, form submit, navigation sequence).

**Evolve:** If a macro fails, identify the correct new selectors, then call `macro_save` to update it. Don't patch the current session in isolation.

**Nominate:** If a manual workflow repeats, record an Advisor observation so the user can decide whether to save it.

## Parameterized Macros

Macros support `{{arg}}` placeholder substitution. Pass `params={"arg": "value"}` to `macro_run`. Use this for workflows that are structurally identical but operate on different values (different search terms, different form fields, different target URLs).
