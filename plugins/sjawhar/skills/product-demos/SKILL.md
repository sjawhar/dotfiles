---
name: product-demos
description: Use when making a product demo video, a narrated walkthrough or a product announcement with video. Also use for asciinema or screen recordings, voiceover or TTS narration, and cast-to-video conversion.
---

# Product demo videos

Read [references/pipeline.md](references/pipeline.md) when you install the tools or run a step below: it has the commands and settings.

## Production rules

1. Do not stretch video to fit narration: no `setpts`, no frame cloning, no tail holds (reviewers find them by frame hashing). Pad the audio with silence instead.
2. Record in small independent sections: one `.cast` and one narration MP3 each, joined by an edit-decision-list (EDL) script (`build.py`: raw-second windows, narration offsets). Re-record a weak section alone.
3. Show the payoff in the first ~5 seconds: result first, then the recipe. Do not open on slow setup typing.
4. Measure the capture rate, not the nominal fps: screen capture under load drops frames silently. For each section, compare file duration with wall-clock time. A ratio materially off 1:1 is a reshoot, not a post fix. asciinema is exempt: it records terminal events, not frames.
5. An error on screen means a reshoot of that section. Do not trim around it: frame and duration checks cannot read error text, so missed error frames ship. Fix the root cause (usually stale state from an earlier take) and re-record against a clean destination.
6. Effect comes after cause: a spliced clip that reads a path comes after the clip that creates it.
7. Narration claims only what the screen shows. Cut an overclaim; do not soften it. A silence of 2s or more is fine over live typing. Over a frozen frame, cut the video.
8. When several demo sessions share a host, boot local Docker sandboxes one at a time: concurrent boots overload it.

## Record

- Terminal: one logical section per `.cast`, 120x35, dark theme. Keep a script of what to type, but do not over-rehearse. Type `# Section: ...` comments to mark trim points.
- GUI or editor: after an input you cannot see land, measure its effect (a panel's bounding box, clip duration against wall clock, a counter, terminal DOM text) instead of trusting the keypress. When a take fails, measure the artefact before the next try.
- Playwright, CDP and keyboard chords can deliver input to the wrong place and fail silently, and a readiness wait on text can never match. When one hits, apply the workaround in pipeline.md's "Input and readiness traps"; do not tear down the live sandbox.
- Set the layout before you record, and check it in the recorded frame: panes that collapse, scroll or resize between takes clip beats and break crops.
- A cleanup path must not authenticate through the credential store of the thing it cleans up: it fails exactly when you need it. Give it a credential that still works when that environment is broken, or run it from a separate job with its own credential.

## Cut, narrate, assemble

1. Convert each cast to MP4. agg compresses idle time: find trim points from thumbnails, not from cast timestamps.
2. Cut each clip hard to its real action, at constant fps. Measure its duration.
3. Write the narration after you cut, to that length. Split long lines and put each part at the moment it describes (EDL offsets). Make the build fail if a part overruns its clip. The listener hears each sentence once and knows none of the project's words: say what a project name is before you use it.
4. Check the narration before you voice it, and record both results in `<video>.src/README.md`:
   - Lint: run `ste-lint <file>` (`scripts/ste-lint`, on `PATH`) on the narration and fix it to 0 hard violations.
   - Teach-back: give a fresh subagent only the narration and the audience (for example "a technical leader who knows software and the field, and has not followed this project"), with: "As that reader, for each sentence, write in your own words what it tells you. If you must guess what a word or phrase refers to, write 'unclear:' and the word or phrase. Do not mark words that reader knows."
   - Score each sentence 2 (the teach-back matches your meaning, carries its number or example, and the facts support it), 1 (matches, but vague where the facts have a number or example) or 0 (wrong, "unclear", or a claim the facts do not support). Pass: no 0, and at most one 1 per section. Rewrite each 0 and 1 and test again on a new subagent: one that read an earlier version has learned the words.
5. If narration cannot fit, shorten the words. Cut footage only where reality is unwatchably slow (a sandbox boot).
6. Choose the voice first: give the user one labeled A/B video of several voice samples. Take the candidates from the top models of the current Artificial Analysis TTS leaderboard (https://artificialanalysis.ai/text-to-speech/leaderboard, read on the day), across providers you have keys for, and the voices each provider recommends for narration.
7. Generate with the model the user picked, at `mp3_44100_192` or better, at the pace the user wants. Reach it with the model's speed setting and fewer words, and drop a voice that stops sounding natural at that pace.
8. `.strip()` the API key. Catch transport errors without printing headers: httpx otherwise prints the raw key in a traceback.
9. After the recordings are final and before the final render, audit every narration line against the screen: command output, numbers, feature names, workflow.
10. Normalize all clips (resolution, fps, pixel format, stereo 44100 Hz AAC) before concat.

## Review before it ships

Use the `task` tool to run two reviewers in parallel on the finished MP4; each finds blockers the other misses.

- Strategy (`oracle`): can the audience do the thing afterwards? What cut loses nothing? What does narration claim that the screen does not show? Is a video the right format at all? What goes at 30% shorter?
- Vision (a `task` subagent reading sampled frames): sample every 2-3s, densely at section boundaries. Transcribe the screen and look for error text that frame statistics cannot read. Check legibility at 640x360, dead air, visible jumps at section cuts, truncated text, sensitive data.
- Read small UI text (labels, form fields, terminal chrome) only from a crop at native resolution or 2x, not from a full-frame read, pass or fail. If two reads disagree, crop and read again.

Verdicts: PASS, CUT (with timestamps) or RESHOOT. Cuts are the expected result.

- Before review, the author discloses each section's wall-clock and file duration, and any frame holds.
- A re-review checks the delta and every boundary the EDL change moved: an EDL change moves all downstream boundaries.
- Keep `<video>.src/` next to the accepted final, not in `/tmp`: casts, EDL script, narration MP3s, and a README with exact rebuild steps.

Un-narrated PR demo: use pipeline.md's quick recipe and post the `asciinema upload` URL in the PR.
