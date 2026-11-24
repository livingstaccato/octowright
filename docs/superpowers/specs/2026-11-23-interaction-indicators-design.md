# Octowright interaction indicators

Show manual and automated pointer interactions in the live browser and in dashboard playback, with independent controls.

## Proposed behavior

Each browser can enable `show_interactions` for a live overlay and `record_interactions` for playback metadata. Both are opt-in and can change while the browser is open. The dashboard has its own Show interactions switch when a recording contains that metadata. Browser-drawn markers appear in the captured video; switching the dashboard overlay off cannot erase markers already present in those pixels.

Use a short expanding ring for a click or tap, a distinguishable labelled ring for right and middle buttons, a double-ring for a double click/tap, and a held ring that changes to Hold after 500ms. A drag keeps a contact marker through movement and fades on release. Wheel input shows a brief direction cue. Labels and shapes distinguish gestures as well as color. Multiple touch contacts remain distinct.

The overlay does not intercept pointer input, change focus or prevent application events. Navigation, new tabs and frames receive the current session setting. Disabling it removes visible markers and stops animation work. Session relaunch/handoff retains the requested settings through the existing launch-options contract.

## Approach

Use a shared page-event observer for all browser engines. It covers manual and automated input. Tool-only annotations would omit manual interactions; a browser-protocol overlay would require an engine-specific implementation. Keep event observation separate from rendering so either output can be enabled independently.

Store bounded interaction metadata in a per-session sidecar beside existing recordings: relative timestamp, coordinates, viewport dimensions, pointer/contact type, button, gesture phase and a bounded contact identifier. Validate page-supplied fields and event rates on the host. Carry no page text, selectors or typed values in this channel. Keep these observations separate from replayable macro actions. Page-world observations remain best effort and page-observable.

The dashboard maps recorded coordinates onto the displayed video viewport and synchronizes markers with playback time, including pause, seek, resize and playback-speed changes. It must not render a second marker over an event whose indicator was already drawn into the video. Record the relevant live-overlay state alongside events. Older recordings keep their current playback behavior and show the control as unavailable when no interaction metadata exists.

## Validation

Use deterministic scheduler tests for press/hold/release timing, cancellation, double-click de-duplication, contact cleanup, event-rate limits and mode changes. Run real local-page tests on Chromium, Firefox and WebKit for manual-style native input and Octowright-driven actions, navigation, frames, scaled video, and disabling the overlay. Verify that enabling feedback does not change the application's observed input sequence. Test sidecar containment/schema, dashboard seek/pause/speed synchronization, and launch/relaunch/replay/export contracts for any exposed settings. Build and inspect a recorded-video sample with the live overlay enabled.

Update the changelog, current release highlights, tool documentation and packaged-asset/type-check guards for the feature.
