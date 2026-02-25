---
name: product-demos
description: "Use when creating narrated product demo videos from terminal recordings. Triggers on: asciinema, screen recording, product video, demo video, narrated walkthrough, voiceover, TTS, cast-to-video, product announcement with video"
---

# Product Demo Videos

Produce narrated product demo videos from asciinema terminal recordings. Pipeline: `.cast` → MP4 → ElevenLabs voiceover → synced narrated video.

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
- If a command errors on camera, that's usually fine — re-record only if the error is misleading

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

## Video Assembly

### Syncing Video + Audio

Speed-adjust video to match audio duration. Terminal recordings tolerate wide speed ranges:

```python
video_dur = get_duration(video_path)
audio_dur = get_duration(audio_path)
pts = max(0.25, min(4.0, video_dur / audio_dur))
inv_pts = 1.0 / pts

ffmpeg ... -filter_complex
  "[0:v]setpts={inv_pts}*PTS,...[v];[1:a]volume=2.0,aformat=channel_layouts=stereo[a]"
  -map "[v]" -map "[a]"
  -c:a aac -b:a 192k -ar 44100 -ac 2
```

**Acceptable speed ranges:**
- 0.5x–2.0x: imperceptible for terminal recordings
- 0.3x–0.5x: fine for "reading the screen" moments (diagnostics output)
- >3x: video becomes unwatchably fast — trim the narration instead

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


## Multi-Agent Coordination

For recording + production split across agents, use file-based mailbox:

```
~/.agent-mail/project-name/
  001-recording-requests.md   # Production → Recording: what to record
  002-recording-status.md     # Recording → Production: what's done, issues
  003-followup.md             # Iterate as needed
```

Each message includes: date, what's done, what's needed, file locations.

New recordings go directly to the shared recordings directory. Production agent polls for new files.

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
| Picking voice without samples | Always generate A/B comparison video for user |

## Quick PR Demo Videos (Lightweight)

For simple feature demo recordings attached to PRs (no narration needed):

### Record

```bash
asciinema rec /tmp/demo.cast --cols 120 --rows 35
# Demonstrate the feature, then exit
```

### Convert to MP4

```bash
agg --font-size 24 --theme monokai /tmp/demo.cast /tmp/demo.gif
ffmpeg -y -i /tmp/demo.gif \
  -movflags faststart -pix_fmt yuv420p \
  -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' \
  -c:v libx264 -preset slow -crf 15 -tune stillimage \
  /tmp/demo.mp4
```

### Upload and Attach to PR

**Option 1 (preferred): Upload to asciinema.org + post as PR comment**

```bash
asciinema upload /tmp/demo.cast
# Copy the URL, then post as a PR comment:
gh pr comment $PR_NUM --repo $OWNER/$REPO --body '## Demo Video

https://asciinema.org/a/XXXXX'
```

**Option 2: Post mp4 URL as PR comment (GitHub auto-renders inline)**

Post the raw .mp4 URL on its own line in a PR comment. GitHub renders it as an
inline video player.

```bash
gh pr comment $PR_NUM --repo $OWNER/$REPO --body '## Demo Video

https://github.com/OWNER/REPO/releases/download/TAG/demo.mp4'
```

**DO NOT create GitHub releases just to host demo videos.** Release assets pollute
the releases page and do not render inline. Prefer asciinema.org for terminal
recordings.
