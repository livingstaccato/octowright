---
description: Replay a saved Octowright macro against a browser session.
argument-hint: <macro-name> [against session <instance-id-or-persona>]
---

You are replaying an Octowright macro. First read the parent `SKILL.md`,
then follow this workflow:

Task: $ARGUMENTS

## Steps

1. **Resolve the macro.** Parse `$ARGUMENTS` for a macro name (and optionally
   a target session or persona). If no macro name is given, call `macro_list`
   and ask the user to choose.

2. **Resolve the session.** If a session or persona was specified, find the
   matching `instance_id` from `octowright_status`. If none exists, launch one:
   use `browser_suggest_for_url` if the macro has a target URL, then
   `browser_launch`.

3. **Run the macro.**
   ```
   macro_run(instance_id, name=<macro-name>)
   ```
   Pass `params={}` if the macro uses `{{arg}}` placeholders and values
   were provided in the task description.

4. **Verify.** After the macro completes, use `browser_snapshot` to confirm
   the expected end state. If the macro failed, check the error message —
   a selector failure usually means the site changed.

5. **Repair (if a selector failed).** Don't immediately re-record. Work the
   resilience ladder:
   - `macro_repair_preview(name)` to see which action indices have a stored
     semantic replacement (`click` → `click_by`, etc.).
   - For an offered index, `macro_repair_apply(name, action_index=<n>)` rewrites
     that brittle selector into its semantic form, drops the stale CSS, and saves
     in place. Then `macro_run` again.
   - Only if the flow itself moved/restructured (no stored semantic locator, or
     repair still fails) tell the user the macro needs **re-recording** — macros
     are a disposable cache, not hand-maintained code.

6. **Report.** Tell the user whether the macro succeeded, how long it took,
   and the final page state. If it failed, include the specific action that
   failed and whether you repaired it, suggested a repair, or recommend re-recording.
