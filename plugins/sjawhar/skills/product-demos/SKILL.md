---
name: product-demos
description: "Use when creating narrated product demo videos from terminal recordings. Triggers on: asciinema, screen recording, product video, demo video, narrated walkthrough, voiceover, TTS, cast-to-video, product announcement with video"
---

# Product Demo Videos

Produce narrated product demo videos from asciinema terminal recordings. Pipeline: `.cast` → MP4 → ElevenLabs voiceover → synced narrated video.

## Production Standard (survived two-reviewer rounds, 2026-09-12)

Four videos went through independent review rounds; every rule below traces to a finding that cost a re-cut. The full standard and review protocol used that night are archived beside the accepted videos (`~/newsletter-videos/process/`).

1. **Never stretch video to fit narration.** No `setpts`, no frame cloning, no tail holds. A 4x stretch reads as ~5-second recordings frozen while the voice keeps talking; reviewers catch it with frame hashing every time. Cut the video hard to its real action, then write narration AFTER cutting, to each clip's measured length, placed at the moment it describes. Pad audio with silence, never video with frames. A build script should fail if a narration part overruns its clip.
2. **Record in small independent sections** — one `.cast` and one narration MP3 per section, assembled by an edit-decision-list script (`build.py` pattern: raw-second windows + narration placement offsets, full rebuild in seconds). A weak section is re-recorded alone; review feedback costs one section, not the video.
3. **Open on the payoff within ~5 seconds.** Leading with slow letter-by-letter setup typing buried one video's payoff at 10.7s and forced a re-cut. Show the result early; the recipe can follow (result-then-recipe reviewed as clearer than chronology).
4. **Verify the capture rate, not the file's nominal fps.** Screen capture under load silently drops frames and produces a time-compressed file (observed: 199s of typing in a file that claims 20fps, everything appearing instantly). Compare file duration against wall clock per section; a ratio materially off 1:1 is a re-shoot. asciinema is timing-accurate by construction and exempt.
5. **An erroring surface on camera is an automatic RESHOOT** of that section. One cut shipped with a legible `Error: ... already exists` on screen for 3.3s — every mechanical check (frame uniqueness, silence, legibility) passed; only a reviewer transcribing frames caught it. Fix the root cause (stale state from an earlier take), re-record against a clean destination.
6. **Causality on screen: effect never precedes cause.** When splicing beats between cuts, a clip that reads a path must come after the clip that creates it. A reviewer flagged a spliced diff beat reading a directory whose compose ran five seconds later.
7. **Narration claims only what the screen shows.** "At run time it composes…" with no run on camera is an overclaim: cut the words, not soften them. Silence gaps ≥2s are fine over live typing, never over a frozen frame nobody is reading.
8. **Sequence local Docker sandbox boots** one at a time when multiple demo sessions share a box; concurrent boots thrashed a 247GB machine to load 539.

## Review Protocol

Before a video ships, run two reviewers in parallel on the finished MP4 (they answer different questions; both returned unique blocking findings):
- **Strategic (oracle):** can the target audience do the thing afterwards; what can be cut with no loss; is anything claimed that is not shown; is this the right surface; what goes if it had to be 30% shorter.
- **Vision (frame-reading):** sample every 2-3s plus densely at section boundaries; TRANSCRIBE what the screen says (hunt error text explicitly — frame statistics cannot read); legibility at half resolution (640x360); dead air, seams, truncation; anything sensitive on screen.

Verdicts: PASS / CUT (with timestamps) / RESHOOT. Cuts are the expected outcome, not a failure. Re-reviews check the delta plus every boundary the edit shifted (EDL changes move all downstream boundaries). Authors disclose per-section wall-clock vs file-duration and any frame holds up front — disclosure is what makes timing verifiable.

**Preserve the re-record path beside the accepted final:** a `<video>.src/` dir with the casts, the EDL script, narration MP3s, and a README with exact rebuild steps. `/tmp` gets swept; accepted videos and their sources belong somewhere durable.

## Pipeline Overview

```
.cast files (asciinema recordings)
    ↓
agg → GIF → ffmpeg → MP4 clips (per section)
    ↓
Trim clips to interesting parts (thumbnail-guided)
    ↓
ElevenLabs API → per-section MP3 narration
    ↓
ffmpeg sync (speed-adjust video to match audio)
    ↓
Normalize + concatenate → final MP4
```

## Setup

```bash
# Install agg (asciinema gif generator) — MUST use --git, not crate name
cargo install --git https://github.com/asciinema/agg

# Python deps in a venv
uv venv /tmp/demo/venv
source /tmp/demo/venv/bin/activate
uv pip install elevenlabs

# Verify
which agg ffmpeg ffprobe
```

**Gotcha:** `cargo install agg` installs a DIFFERENT crate (a library). Must use `--git`.

## Recording with asciinema

```bash
asciinema rec /tmp/demo/recordings/section-name.cast
# Terminal size: 120x35 recommended for consistency
# Theme: set your terminal to a dark theme before recording
```

**Key principles:**
- Record one logical section per file
- Keep a script of what to type, but don't over-rehearse
- Comments (`# Section: ...`) typed into terminal help with trim-point discovery later
- **An erroring command on camera is a re-record of that section**, not a judgment call. A legible error shipped past every mechanical check once (frame uniqueness, silence, legibility) and only a frame-transcribing reviewer caught it; the usual root cause is stale state from an earlier take, so re-record against a clean destination rather than trimming around it.

## Recording a GUI or an Editor (not a terminal)

asciinema does not apply; you are driving a browser or an editor with an automation driver and capturing the screen.

**Automation input does not go where the driver believes. Three separate cases, one rule.** Playwright's input reaches the *page* it targets, not necessarily the thing on screen, and it fails silently. Seen three ways in one night: (1) `keyboard.type` into an agent running in the VS Code terminal is swallowed, so the prompt stays empty and the caller waits out its full timeout on an answer nobody asked for; (2) a CDP-dispatched click does not move **X input focus**, so an `xdotool type` after it lands wherever focus already was; (3) a keybinding chord (`F9`) pressed while focus sits in a sidebar webview is eaten by the webview and never reaches the workbench, so the panel does not maximize and the code carries on believing it did. The fix is the same each time: click the target with real X input (`xdotool mousemove; click`), then type or press at the X level.

**The rule is not "use xdotool". The rule is: after any input you cannot see land, measure the effect, never trust the keypress.** A panel's bounding-box height, a clip's duration against wall clock, the counter on a card, the text in the terminal DOM. Those three cases cost about ten takes between them, and every one ended the moment something was measured instead of assumed: the clip length that proved a typing fix had never once run, the panel height that proved `F9` had never once fired.

**Never key readiness on text that lives in a viewport-dependent region.** xterm renders only the visible rows, so a footer hint (`bypass permissions (shift+tab to cycle)`) is not in the DOM on a short panel and a wait for it times out against a terminal whose prompt is plainly up in the frame. Wait on any startup line, then settle; if nothing matches, settle and continue rather than abandoning a live sandbox.

**Verify the capture rate, not the file's nominal fps.** A screen capture under load silently drops frames and produces a time-compressed file: 199 seconds of real typing arrived in a file claiming 20fps with everything appearing instantly, and `frames = duration x nominal_fps` looked correct. Compare file duration against wall clock per section; a ratio materially off 1:1 is a re-shoot, not a post fix.

**Compose before you record.** Panes that collapse, scroll, or resize between takes will clip a beat: a crop calibrated on a take where a side pane existed cut a later take mid-line, and a sidebar left expanded pushed the button the section is about below the fold. Check the frame, not the app.

**The same lesson, pointed at your own diagnosis: read the artefact, not the theory.** Three runs failed at what looked like a typing bug. The clip length said otherwise (198s, about the 180s readiness timeout plus overhead), which meant the typing fix had never once been exercised. Measuring ended it; a fourth attempt would not have.

**A cleanup path must not authenticate through the same credential store as the thing it cleans up.** It fails exactly when it is needed. A harness killed by the OOM-killer took the keyring with it, so the wrapper's own eval-set sweep could not authenticate and left a live sandbox orphaned on the cluster; the namespace-protection admission policy correctly refused the blunt `kubectl delete ns` route. Give the sweep a credential path that survives the harness, or an external reaper that does not share its failure mode.

## Cast → MP4 Conversion

```bash
# Step 1: Cast → GIF (agg compresses idle time automatically)
agg --font-size 24 --theme monokai input.cast output.gif

# Step 2: GIF → MP4 (terminal-optimized encoding)
ffmpeg -y -i output.gif \
  -movflags faststart -pix_fmt yuv420p \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -c:v libx264 -preset slow -crf 15 -tune stillimage \
  output.mp4
```

**Critical settings:**
- `-crf 15` (not 18 or 23) — terminal text needs near-lossless quality
- `-tune stillimage` — optimizes for low-motion content (terminal = mostly static)
- `scale=trunc(iw/2)*2:trunc(ih/2)*2` — ensures even dimensions for h264

**Gotcha:** agg compresses idle time, so .cast timestamps ≠ MP4 timestamps. Find trim points via thumbnails, not math.

## Finding Trim Points

```bash
# Generate thumbnails at intervals
for t in 0 5 10 15 20 30 40 50 60; do
  ffmpeg -y -ss $t -i full.mp4 -frames:v 1 -q:v 5 thumb_${t}s.jpg 2>/dev/null
done
```

Then use `look_at` or manual inspection to identify section boundaries. Trim with:

```bash
ffmpeg -y -i full.mp4 -ss $START -to $END \
  -c:v libx264 -crf 15 -tune stillimage -pix_fmt yuv420p -an \
  trimmed.mp4
```

## Narration Script Structure

Write narration as a Python data structure for programmatic generation:

```python
SECTIONS = [
    {
        "id": "1a_feature_intro",
        "title": "Feature Name",        # → title card
        "narration": "Script text here. Use <break time=\"0.8s\" /> for pauses.",
        "video": {
            "source": "recording-full.mp4",
            "trim": (start_sec, end_sec),
        },
    },
]
```

**Script-to-screen audit (MANDATORY before final render):**
After all recordings are finalized, compare every narration line to what's actually visible on screen. Pre-written scripts WILL diverge from actual recordings. Common mismatches:
- Command output differs from what narration describes
- Specific numbers/stats don't match (e.g., "resisted" vs "ignored")
- Feature names differ (e.g., "slash run-inspect" vs "running-tasks")
- Described workflow doesn't match what the recording shows

## ElevenLabs Voice Generation

### Voice Selection (DO THIS FIRST)

Generate comparison samples before committing to a voice:

```python
from elevenlabs import ElevenLabs, VoiceSettings, save

SAMPLE_TEXT = "Your representative 2-3 sentence sample."

for voice_id, name in [
    ("CwhRBWXzGAHq8TQ4Fs17", "Roger"),
    ("iP95p4xoKVk53GoZ742B", "Chris"),
    ("cjVigY5qzO86Huf0OWal", "Eric"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
]:
    audio = client.text_to_speech.convert(
        voice_id=voice_id, text=SAMPLE_TEXT,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_192",
        voice_settings=VoiceSettings(
            stability=0.75, similarity_boost=0.85,
            style=0.0, speed=0.92, use_speaker_boost=True,
        ),
    )
    save(audio, f"sample_{name}.mp3")
```

Build a comparison video with labels so the user can A/B in one file:

```bash
ffmpeg -y \
  -f lavfi -i "color=c=0x1a1a2e:size=1280x720:duration=${dur}:rate=24" \
  -i sample.mp3 \
  -filter_complex "[1:a]volume=2.0,aformat=channel_layouts=stereo[a];
    [0:v]drawtext=fontfile=${FONT}:text='${NAME}':fontsize=48:
    fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p[v]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -crf 18 -c:a aac -b:a 192k -ar 44100 -ac 2 \
  -shortest labeled_sample.mp4
```

### Generating Narration

```python
audio = client.text_to_speech.convert(
    voice_id=VOICE_ID,
    text=section_text,
    model_id="eleven_turbo_v2_5",      # Best for English narration
    output_format="mp3_44100_192",      # 192kbps — 128 sounds bad
    voice_settings=VoiceSettings(
        stability=0.75,                  # 0.6-0.8 for narration
        similarity_boost=0.85,
        style=0.0,                       # Keep at 0 — reduces artifacts
        speed=0.92,                      # Slightly slower for clarity
        use_speaker_boost=True,
    ),
    previous_text=prev[-200:],           # Cross-section continuity
    next_text=nxt[:200],
)
save(audio, output_path)
```

**Critical audio settings:**
- `mp3_44100_192` minimum — 128kbps sounds tinny/compressed
- `eleven_turbo_v2_5` model — more natural than `multilingual_v2` for English
- `pcm_44100` (lossless) requires Pro plan
- Mono output from API — must convert to stereo + boost volume for video

### Pronunciation

ElevenLabs handles most acronyms. For problem terms, use alias substitution in text:
- `"jj"` → `"jay-jay"`, `"CLI"` → `"C L I"`, `"OAuth"` → `"Oh-Auth"`
- `"uv sync"` → `"you-vee sync"`, `"tl run"` → `"T L run"`

### Fallback: gTTS

If no ElevenLabs key, `pip install gTTS` provides free Google TTS. Lower quality but unblocks the pipeline. Strip `<break>` tags (unsupported) and replace with periods.

### Key hygiene

`.strip()` the API key on read and catch transport errors without printing headers: the stored key has carried trailing whitespace, which httpx rejects as an illegal header value with a traceback that prints the raw key into the transcript.

## Video Assembly

### Syncing Video + Audio

**Do not speed-adjust video to match audio** (see Production Standard rule 1 — stretched or frame-cloned video fails review). Sync the other way:

1. Cut each clip hard to its real action (hard cuts only, constant fps, no `setpts`).
2. Measure the cut clip's duration.
3. Write narration to fit that length; split long lines across the clip and place each part at the moment it describes (offsets in the EDL).
4. Pad the audio track with silence to the clip length. Silence over live typing is fine; over a frozen frame it is dead air — cut the video instead.

If narration genuinely cannot fit, shorten the words. Only when a section is unwatchably slow in reality (a sandbox boot) do you cut footage out — never slow or stretch what remains.

### Normalization for Concat

**ALL clips MUST be normalized before concatenation.** ffmpeg concat demuxer requires identical:
- Resolution (scale + pad to target)
- FPS (`fps=10` is fine for terminal)
- Pixel format (`format=yuv420p`)
- Audio: stereo, 44100Hz, AAC

```bash
ffmpeg -y -i clip.mp4 \
  -vf "scale=${W}:${H}:force_original_aspect_ratio=decrease,
       pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=0x1a1a2e,
       fps=10,format=yuv420p" \
  -c:v libx264 -crf 15 \
  -c:a aac -b:a 192k -ar 44100 -ac 2 \
  normalized.mp4
```

**Gotcha:** ffmpeg `scale` filter uses `:` separator, NOT `x`. `scale=1756:1208` ✅, `scale=1756x1208` ❌.

### Title Cards

```bash
ffmpeg -y -f lavfi \
  -i "color=c=0x1a1a2e:size=${W}x${H}:duration=3:rate=10" \
  -f lavfi -i "anullsrc=r=44100:cl=stereo" \
  -vf "drawtext=fontfile=${FONT}:text='Section Title':
       fontsize=52:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,
       format=yuv420p" \
  -c:v libx264 -crf 15 -c:a aac -b:a 192k -t 3 title.mp4
```

### Concatenation

```bash
# Build concat list
for clip in normalized_*.mp4; do
  echo "file '$clip'" >> concat.txt
done

ffmpeg -y -f concat -safe 0 -i concat.txt -c copy final.mp4
```


## Common Mistakes

| Mistake | Fix |
|---------|-----|
| `cargo install agg` installs wrong package | Use `--git https://github.com/asciinema/agg` |
| 128kbps MP3 sounds tinny | Use `mp3_44100_192` (Creator+ plan) |
| Mono audio plays silent on some devices | Always output stereo (`-ac 2`) with volume boost (`volume=2.0`) |
| `scale=WxH` in ffmpeg | Use `scale=W:H` (colon, not x) |
| Narration doesn't match screen | Audit script-to-screen AFTER recordings finalize |
| Concat produces garbage | Normalize ALL clips to same resolution/fps/pix_fmt/audio first |
| Writing narration before recording | Record first, write narration to match |
| Picking voice without samples | Always generate A/B comparison video for user |

## Quick PR Demo Videos (Lightweight)

For simple, un-narrated PR demos:

```bash
asciinema rec /tmp/demo.cast --cols 120 --rows 35
agg --font-size 24 --theme monokai /tmp/demo.cast /tmp/demo.gif
ffmpeg -y -i /tmp/demo.gif -movflags faststart -pix_fmt yuv420p \
  -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -c:v libx264 -preset slow \
  -crf 15 -tune stillimage /tmp/demo.mp4
asciinema upload /tmp/demo.cast  # preferred; post the returned URL in the PR
```
