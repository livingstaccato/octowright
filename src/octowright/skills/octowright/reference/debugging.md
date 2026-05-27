---
name: debugging
description: Reference for the Octowright debugging hierarchy — aria-tree snapshots, iframe handling, golden diffs, and selector strategy.
---

# Debugging Reference

## Hierarchy: Try These in Order

### 1. `browser_snapshot` → Aria-Tree

The accessibility tree is more stable than the raw DOM. Use it first when an action fails (selector not found, click misses, element unreachable).

```
browser_snapshot(instance_id, selector="body")
```

Read the tree output to find the stable `role`, `name`, or `aria-label` for the target element. Prefer aria attributes over CSS class-based selectors — they survive redesigns.

### 2. `browser_list_frames` → Check for Iframes

If `browser_snapshot` shows the element isn't present in the main frame, the target may be inside an iframe.

```
browser_list_frames(instance_id)        # list available frames
browser_switch_frame(instance_id, url)  # switch context to the frame
```

Re-run `browser_snapshot` after switching to confirm the element is now visible.

### 3. `golden_assert` → Structural Comparison

When the failure is visual or structural (layout changed, expected section missing), compare against a saved golden baseline.

```
golden_assert(instance_id, name="my-baseline")
```

Diff output identifies which aria-tree nodes changed. Use this to distinguish a real regression from a selector that just needs updating.

## Role-Based Selectors

Prefer `browser_click_by` with `role=` / `label=` / `test_id=` over CSS selectors when the site exposes proper ARIA roles:

```
browser_click_by(instance_id, role="button", label="Submit")
browser_click_by(instance_id, test_id="login-btn")
```

Role-based selectors survive DOM restructuring; CSS selectors don't.

## Waiting for State

Use `browser_wait_for` rather than fixed sleeps:

```
browser_wait_for(instance_id, selector=".results-loaded", timeout_ms=5000)
browser_wait_for(instance_id, text="Welcome back", timeout_ms=3000)
browser_wait_for(instance_id, expression="document.readyState === 'complete'", timeout_ms=10000)
```

`expression=` accepts any JS predicate — useful for SPA state that doesn't surface a DOM selector.
