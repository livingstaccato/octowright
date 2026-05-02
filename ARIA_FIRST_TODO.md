# ARIA-First Macros TODO

## Task 1: Semantic Metadata Capture
- [x] Implement `_resolve_semantic_metadata` in `BrowserSession`
- [x] Update `click`, `fill`, and `type_text` to record metadata
- [x] Verify metadata appears in JSONL logs
- [x] Commit

## Task 2: ARIA-First Macro Playback
- [x] Update `_ACTION_MAP` to include `click_by` and `fill_by`
- [x] Update `_dispatch_simple` to prioritize semantic locators
- [x] Implement fallback logic
- [x] Verify with a mock DOM change test
- [x] Commit

## Task 3: Optimized Recording (Clean Macros)
- [x] Update `_substitute_in_action` to handle semantic fields
- [x] Update `save_macro` to prioritize semantic attributes
- [x] Verify saved macro JSON structure
- [x] Commit
