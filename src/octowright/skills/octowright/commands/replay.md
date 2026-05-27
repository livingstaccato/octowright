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
   a selector failure usually means the site changed; use `macro_repair_preview`
   to get a suggested fix.

5. **Report.** Tell the user whether the macro succeeded, how long it took,
   and the final page state. If it failed, include the specific action that
   failed and the repair suggestion.
