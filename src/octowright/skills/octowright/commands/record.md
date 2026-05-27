---
description: Record a new Octowright macro by driving a live browser workflow and saving the action sequence.
argument-hint: <natural-language description of the workflow to record>
---

You are recording a new Octowright macro. First read the parent `SKILL.md`
and `reference/macros-and-advisor.md`, then follow this workflow:

Task: $ARGUMENTS

## Steps

1. **Check existing macros.** Run `macro_list` — if a matching macro exists,
   confirm with the user before overwriting.

2. **Bootstrap.** Call `octowright_status`, inspect the `advisor` block.

3. **Launch.** Use `browser_suggest_for_url` if needed, then `browser_launch`.
   This is agent-internal work — use headless or a clean profile.

4. **Drive the workflow.** Perform every step of the target workflow using
   MCP browser tools. Note each action's selector and intent as you go.

5. **Verify the workflow completes.** Use `browser_snapshot` to confirm the
   expected end state is reached.

6. **Save the macro.** Call `macro_save(name, actions=[...])` with the action
   sequence you just drove. Choose a stable, slug-style name matching the
   `signature` convention from `reference/macros-and-advisor.md`.

7. **Lint the macro.** Call `macro_lint(name)` to check for fragile selectors
   or missing wait conditions. Address any warnings before declaring done.

8. **Close the browser.** Call `browser_close` — this was agent-internal work.

9. **Report.** Tell the user the macro name and a one-line summary of what it
   does. Show the `macro_lint` output if there were any warnings.
