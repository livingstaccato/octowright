# Goldens

Goldens are saved accessibility-tree baselines.

## Core Tools

- `golden_save`
- `golden_assert`
- `golden_verify_loop`
- `golden_list`
- `golden_delete`

## Capture vs Verify Policy

Use one of these modes intentionally:

- Capture mode: call `golden_save` when you want to write/update baseline.
- Verify mode: call `golden_verify_loop` or `golden_assert` when you want drift checks.

`golden_verify_loop` supports:

- `save_if_missing=False` (default): verify-only behavior.
- `save_if_missing=True`: if missing, writes a new baseline and returns `saved: true`.

CI safety:

- If `CI=true`, `save_if_missing=true` is refused by design.

## Response Semantics

From `golden_verify_loop`:

- `saved: true` when a new golden was created.
- `saved: false` when no write occurred.
- `missing: true` when golden does not exist and save was not requested.
