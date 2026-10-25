# Producing an explainer

## Tools

- **Animation:** [Revideo](https://re.video) (TypeScript scenes rendered to MP4 by headless Chrome and ffmpeg). Start from an earlier explainer's Revideo project, not an empty one: its `<subject>.src/README.md` names the project and its rebuild command. The Verified Linux explainer's project has charts drawn from data, diagrams that build node by node, terminal clips timed to the narration, and voice timing. Copy the project into your own directory and change the copy.
- **Screen recordings:** browser pages with the `browser` tool's recording, or Playwright; terminals with asciinema and `agg`. Record the real system, cut hard to the action, and keep each clip's wall-clock and file duration for the review.
- **Render on a cloud VM you create for it**, never on a shared box; delete the VM when the video ships.

## Voice

- `eleven_v4` has no speed setting. Put the tag `[very fast, rapid-fire delivery]` before each line, which gives Max about 183 words a minute, then speed the audio with ffmpeg `atempo` to reach about 210. Measure each line's words a minute from the audio; do not trust a nominal setting.
- `.strip()` the API key (`secrets ELEVENLABS_API_KEY -- ...`), and never print request headers.
- Pad the audio with silence to fit the picture; never stretch or hold video frames to fit the audio.

## Review

Before it ships, run the product-demos skill's two reviewers in parallel on the finished MP4: strategy (does it teach the thing, what can be cut, what does the narration claim that the screen does not show) and vision (sample every 2 to 3 seconds; read the screen; check for still frames, empty frames, cut text, error text). Disclose each clip's durations and any held frame to them. Fix every CUT and re-review the changed boundaries.

## Deliver

- Upload the video to the stream's Dispatch issue as an artifact named `explainer-<subject>.mp4`, and its script as `explainer-<subject>-script.md` with front matter `title: <the title the viewer sees>`. The streams dashboard lists every `explainer*.mp4` artifact on a stream issue under that title.
- A new version of the same artifact replaces the old video; never upload a second name for the same subject.
- Open the dashboard's explainer page and play the video before you report it.
- Keep `<subject>.src/` next to the project: script, narration audio, clips, and the exact rebuild command.
