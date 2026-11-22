---
name: product-demos
description: Use when making a product demo video, a narrated walkthrough or a product announcement with video. Also use for asciinema or screen recordings, voiceover or TTS narration, and cast-to-video conversion.
---

# Product demo videos

**Done when:** you have used the `task` tool to run the two reviewers of step 3 ("Review before it ships") in parallel on the finished MP4; each finds blockers the other misses.

Read [references/pipeline.md](references/pipeline.md) when you install the tools or run a step below: it has the commands and settings.

## Production rules

1. Do not stretch video to fit narration: no `setpts`, no frame cloning, no tail holds (reviewers find them by frame hashing).
   - Pad the audio with silence instead.
2. Record in small independent sections: one `.cast` and one narration MP3 each, joined by an edit-decision-list (EDL) script (`build.py`: raw-second windows, narration offsets).
   - Re-record a weak section alone.
3. Show the payoff in the first ~5 seconds: result first, then the recipe.
   - Do not open on slow setup typing.
4. Measure the capture rate, not the nominal fps: screen capture under load drops frames silently. For each section, divide the file duration by the wall-clock time. asciinema is exempt: it records terminal events, not frames.
   - Reshoot a section whose ratio is below 0.95 or above 1.05; do not fix it in post.
5. Reshoot a section that shows an error on screen. Do not trim around the error: frame and duration checks cannot read error text, so missed error frames ship.
   - Fix the root cause (usually stale state from an earlier take).
   - Re-record against a clean destination.
6. Effect comes after cause: a spliced clip that reads a path comes after the clip that creates it.
7. Narration claims only what the screen shows.
   - Cut an overclaim; do not soften it.
8. **A silence of 2s or more:** over a frozen frame, cut the video; over live typing, it is fine.
9. When several demo sessions share a host, boot local Docker sandboxes one at a time: concurrent boots overload it.

## 1. Record

- **Terminal:**
  - Put one logical section in each `.cast`.
  - Record at 120x35, in a dark theme.
  - Keep a script of what to type.
  - Do at most one dry run of a section before you record it: one pass through its commands outside `asciinema rec`, which your session's transcript shows.
  - Type `# Section: ...` comments to mark trim points.
- **GUI or editor:**
  - After an input you cannot see land, measure its effect (a panel's bounding box, clip duration against wall clock, a counter, terminal DOM text) instead of trusting the keypress.
  - When a take fails, measure the artefact before the next try.
- Apply the workaround in pipeline.md's "Input and readiness traps" when one of these hits: Playwright, CDP and keyboard chords can deliver input to the wrong place and fail silently, and a readiness wait on text can never match.
- When one of these traps hits, do not tear down the live sandbox.
- Set the layout before you record: panes that collapse, scroll or resize between takes clip beats and break crops.
  - Check it in the recorded frame.
- A cleanup path must not authenticate through the credential store of the thing it cleans up: it fails exactly when you need it.
  - Give it a credential that still works when that environment is broken, or run it from a separate job with its own credential.

## 2. Cut, narrate, assemble

1. Convert each cast to MP4 (`references/pipeline.md`, "Record, convert, trim").
   - Find trim points from thumbnails, not from cast timestamps: agg compresses idle time.
2. Cut each clip hard to its real action, at constant fps.
   - Measure its duration.
3. Write the narration after you cut, to that length.
   - Split a narration line that describes more than one on-screen moment or has more than 20 words.
   - Put each part of a split line at the moment it describes (EDL offsets).
   - Make the build fail if a part overruns its clip.
   - Say what a project name is before you use it: the listener hears each sentence once and knows none of the project's words.
4. Check the narration before you voice it:
   - Lint: run `ste-lint <file>` (`scripts/ste-lint`, on `PATH`) on the narration. Fix it to 0 hard violations.
   - Teach-back: give a fresh subagent only the narration and the audience (for example "a technical leader who knows software and the field, and has not followed this project"), with: "As that reader, for each sentence, write in your own words what it tells you. If you must guess what a word or phrase refers to, write 'unclear:' and the word or phrase. Do not mark words that reader knows."
   - Score each sentence:

     | Score | Teach-back |
     | --- | --- |
     | 2 | Matches your meaning, carries its number or example, and the facts support it |
     | 1 | Matches, but vague where the facts have a number or example |
     | 0 | Wrong, "unclear", or a claim the facts do not support |

     Pass: no 0, and at most one 1 per section.

   - Rewrite each 0 and 1.
   - Then test again on a new subagent: one that read an earlier version has learned the words.
   - Record both results (lint and teach-back) in `<video>.src/README.md`.
5. If narration cannot fit, shorten the words.
   - Cut footage only where reality is unwatchably slow (a sandbox boot).
6. Choose the voice first: give the user one labeled A/B video of several voice samples (`references/pipeline.md`, "Narration").
   - Take the candidates from the current Artificial Analysis TTS leaderboard (https://artificialanalysis.ai/text-to-speech/leaderboard, read on the day): go down it from the top.
   - Skip each model whose provider you have no key for.
   - Stop at three models.
   - Add the voices each provider recommends for narration.
7. Generate with the model the user picked, at `mp3_44100_192` or better, at the pace the user wants (`references/pipeline.md`, "Narration").
   - Reach the pace with the model's speed setting and fewer words.
   - Drop a voice that stops sounding natural at that pace.
8. `.strip()` the API key.
   - Catch transport errors without printing headers: httpx otherwise prints the raw key in a traceback.
9. After the recordings are final and before the final render, audit every narration line against the screen: command output, numbers, feature names, workflow.
10. Normalize all clips (resolution, fps, pixel format, stereo 44100 Hz AAC) before concat (`references/pipeline.md`, "Normalize, title cards, concat").

## 3. Review before it ships

- **Strategy reviewer** (`task` tool, agent `oracle`: a read-only advisor on a non-Anthropic model): can the audience do the thing afterwards? What cut loses nothing? What does narration claim that the screen does not show? Is a video the right format at all? What goes at 30% shorter?
- **Vision reviewer** (a `task` subagent reading sampled frames):
  - Sample every 2-3s, and every 0.5s from 2s before to 2s after each section boundary.
  - Transcribe the screen and look for error text that frame statistics cannot read.
  - Check legibility at 640x360, dead air, visible jumps at section cuts, truncated text, sensitive data.
- Read small UI text (labels, form fields, terminal chrome) only from a crop at native resolution or 2x, not from a full-frame read, pass or fail.
  - If two reads disagree, crop and read again.

Verdicts: PASS, CUT (with timestamps) or RESHOOT. Cuts are the expected result.

- Before review, disclose each section's wall-clock and file duration, and any frame holds.
- In a re-review, check the delta and every boundary the EDL change moved: an EDL change moves all downstream boundaries.
- Keep `<video>.src/` next to the accepted final, not in `/tmp`: casts, EDL script, narration MP3s, and a README with exact rebuild steps.

For an un-narrated PR demo, use pipeline.md's quick recipe. Post its `asciinema upload` URL in the PR.
