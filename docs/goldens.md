# Goldens

A **golden** is a saved accessibility-tree baseline for a page or component.
Subsequent runs diff their accessibility tree against the golden and fail when
they drift, giving you visual-regression-style coverage without the brittleness
of pixel-diff screenshots.

Goldens live under the Octowright config dir: POSIX uses the XDG config dir
`${XDG_CONFIG_HOME:-~/.config}/octowright/goldens/`, and Windows uses
`%APPDATA%\octowright\goldens\`. Override with `OCTOWRIGHT_GOLDENS_DIR`.

## Capture vs verify policy

Goldens are operated in one of two modes — **never both at once**:

- **Capture mode** — `golden_save` writes (or overwrites) the baseline.
- **Verify mode** — `golden_assert` and `golden_verify_loop` diff the live page
  against the saved baseline and raise on mismatch.

The split exists so production CI never silently mints new baselines when a
page subtly changes — that would defeat the entire point of a regression check.

## `golden_verify_loop`

The convenience tool with two relevant knobs:

| Argument | Behavior |
|---|---|
| `save_if_missing=False` (default) | Pure verify. If no baseline exists, raise. |
| `save_if_missing=True` | If no baseline exists, save and return `saved: true`. **Refused under `CI=true`.** |

The CI guard is intentional: in CI, "no baseline" must mean "test fails," not
"oh, I'll just write one and pass."

## Response semantics

`golden_verify_loop` returns:

| Field | Meaning |
|---|---|
| `saved: true` | A new golden was created (only possible with `save_if_missing=True`). |
| `saved: false` | No write occurred. |
| `missing: true` | No golden existed and writing was not requested — verify failed. |

## Tools

| Tool | Purpose |
|---|---|
| `golden_save` | Write or overwrite a baseline. |
| `golden_assert` | Compare live page to baseline; raise on mismatch. |
| `golden_verify_loop` | Verify with an optional retry/wait loop for flake. |
| `golden_list` | Enumerate all saved goldens. |
| `golden_delete` | Remove a saved golden. |

## Related

- [macros.md](macros.md) — pair `golden_assert` with a `[test]`-tagged macro for
  regression coverage in the test suite.
- [troubleshooting.md](troubleshooting.md#golden-verification-mismatches) —
  diagnosis flow for verify failures.
