# Macros

Macros are reusable action sequences derived from recordings.

## Core Tools

- `macro_save`
- `macro_list`
- `macro_run`
- `macro_run_sequence`
- `macro_delete`
- `macro_lint`
- `macro_explain`

## Recommended Flow

1. Launch and perform the flow once.
2. Save it with `macro_save`.
3. Replay with `macro_run`.
4. Run `macro_lint` before promoting to shared workflows.

## Conditional Actions

Supported conditional action families include:

- `if_selector`
- `try`
- `try_each`

Use them to reduce brittleness when DOM variants appear.

## Testing

- `run_test_suite` executes `[test]`-tagged macros and emits JUnit XML.
- CLI equivalent:

```bash
uv run octowright test [path] --out dist/macro-tests.xml
```
