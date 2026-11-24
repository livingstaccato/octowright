# Interaction indicators

Octowright can show manual and automated pointer input in a live browser and
capture metadata for dashboard video playback. Chromium, Firefox and WebKit use
the same observer. Both launch settings default to `false`:

| Setting | Effect |
|---|---|
| `show_interactions` | Draw labelled markers in the page. Native video records these visible pixels when `record_video=true`. |
| `record_interactions` | Write bounded interaction metadata beside the action recording. Does not enable native video. |
| Dashboard **Show interactions** | Independently display metadata over an existing native video. Initially unchecked. |

For a metadata-only playback recording, call:

```python
browser_launch(
    kind="chromium",
    url="http://localhost:3000",
    record_video=True,
    show_interactions=False,
    record_interactions=True,
)
```

Use `browser_quick_launch` with the same options for a persona, or put these
fields in a scenario browser participant. Runtime control requires at least one
boolean; omitted settings retain their value:

```python
browser_set_interaction_visuals(instance_id, show=True, record=True)
browser_set_interaction_visuals(instance_id, show=False)  # keep metadata capture
browser_set_interaction_visuals(instance_id, record=False)
```

The reply reports requested host `show`/`record`, `truncated`, `applied_frames`
and `pending_frames`. Frame counts describe the latest propagation attempt;
retry the same settings if frames remain pending. New documents inherit current
settings. Requested settings survive session relaunch/handoff.

## Try the feedback

Enable `show_interactions` in a local application. Click, right-click and
middle-click an application control; double-click it; tap on a touch-capable
browser; hold a pointer still for 500 ms; drag it; and scroll in either direction.
Labels and shapes distinguish buttons, touch contact IDs, Double, Hold, Drag and
wheel direction as well as color. Native manual input and Octowright actions such
as `browser_click` and `browser_scroll` are observed through the same path:

```python
browser_click(instance_id, selector="#target")
browser_scroll(instance_id, delta_x=0, delta_y=240, selector="#scroll-panel")
```

The observer does not consume input, prevent application events, move focus or
substitute application events. Navigation, tabs and nested frames receive the
current settings. Disable live display to remove its markers and animation work.
Observation runs in the page world and is best effort: application scripts can
block or forge observations. It is feedback, not an audit trail.

## Watch a recording

Set `record_video=True` explicitly alongside `record_interactions=True`, close
the session to finalize its native video, then open its dashboard recording.
Enable **Show interactions** below the video. Pause, seek or change playback speed;
marker state follows the video clock. Without native video there is no video
overlay control. Old recordings without metadata remain playable.

Live indicators already captured in video pixels stay visible with the dashboard
toggle off. Events marked as shown live do not receive a second dashboard marker.
Choose `show_interactions=False` while recording if you want playback feedback
that can be hidden later.

Metadata timing uses an estimated, stable origin for each page's native video;
it is not frame-exact. Coordinates map through the initial viewport, encoded
video size and player letterboxing. Chromium and unchanged Firefox use shrink-only
scaling; WebKit also accounts for initial device scale. Missing geometry or a
Firefox viewport resize suppresses independent markers for affected observations
with an explanation. Any live marks in the pixels remain visible.

## Capture and export

The private `.interactions.jsonl` sidecar belongs to the session action log. It
contains coordinates, viewport sizes, timings, pointer/contact type, button and
gesture phase, plus host page/video identity and timed settings/reset rows. It
contains no selectors, page text or typed values. Capture allows up to 16 active
contacts, 20 drag moves per second per contact and 120 accepted observations per
second per session, with a 64 MiB file ceiling. Loss/truncation is visible in
playback. The authenticated session interactions endpoint paginates by byte cursor;
normal recording cleanup removes the sidecar with its owner.

Observed input never becomes replayable macro actions. Standalone Python,
TypeScript and macro CLI exports create ordinary Playwright contexts: they omit
Octowright feedback/capture and preserve relevant requested settings only as
labelled comments. Replaying application actions through Octowright can observe
the resulting input using the destination session's settings.
