---
description: Launch and orchestrate a named Octowright scenario across multiple browser sessions.
argument-hint: <scenario-name> [with role <role> for <persona>]
---

You are orchestrating an Octowright scenario. First read the parent `SKILL.md`,
then follow this workflow:

Task: $ARGUMENTS

## Steps

1. **Resolve the scenario.** Parse `$ARGUMENTS` for a scenario name. If none
   is given, call `octowright_status` to list available scenarios and ask the
   user to choose.

2. **Dry-run first.** Call `scenario_plan(name)` to see which personas will be
   launched, what fixtures will be applied, and which macros each role will run.
   Confirm with the user if the plan looks right.

3. **Start the scenario.**
   ```
   scenario_start(name)
   ```
   This launches all participant browsers, applies fixtures, and wires up the
   session graph.

4. **Check readiness.** Call `scenario_wait_for_sync` if the scenario requires
   participants to reach a synchronized state before proceeding.

5. **Run scenario macros** (if the task calls for it):
   ```
   scenario_run_macro(scenario_id, macro=<name>, role=<role>)
   ```

6. **Monitor.** Use `browser_tail_recording` or the dashboard WebSocket tail
   to follow live events from each participant session.

7. **Stop when done.**
   ```
   scenario_stop(scenario_id)
   ```
   This closes all participant browsers cleanly.

8. **Report.** Summarize which roles completed successfully, any failures,
   and the recording paths for each session's JSONL.
