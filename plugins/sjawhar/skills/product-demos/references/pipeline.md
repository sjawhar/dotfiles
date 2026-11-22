# Pipeline commands

## Setup

```bash
cargo install --git https://github.com/asciinema/agg   # not `cargo install agg`: that is a different crate
demo=$(mktemp -d)   # scratch: venv, gifs, thumbnails; casts and MP3s go in <video>.src/
uv venv "$demo/venv" && source "$demo/venv/bin/activate"
uv pip install elevenlabs
which agg ffmpeg ffprobe
```

## Input and readiness traps (GUI or editor recording)

For each input in this table, click the target with `xdotool mousemove; click`, then type or press at the X level.

| Input | What goes wrong |
| --- | --- |
| Playwright input | Goes to its target page, not always to the screen, and fails silently |
| `keyboard.type` into an agent in the VS Code terminal | Is lost |
| A CDP click | Does not move X focus |
| A chord (`F9`) with focus in a sidebar webview | Never gets to the workbench |

- Do not wait for readiness on text in a viewport-dependent region (xterm renders only visible rows).
  - Wait up to 30 s for any startup line, then sleep 5 s.
  - If no line appears in those 30 s, sleep 5 s and continue.

## Record, convert, trim

```bash
asciinema rec --window-size 120x35 <video>.src/<section>.cast   # asciinema 3.x; 2.x took --cols/--rows
agg --font-size 24 --theme monokai in.cast out.gif
# -crf 15 (not 18/23): terminal text needs near-lossless. stillimage: static content. scale: even sizes for h264.
ffmpeg -y -i out.gif -movflags faststart -pix_fmt yuv420p \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -c:v libx264 -preset slow -crf 15 -tune stillimage out.mp4
# Thumbnails for trim points; read them with the `read` tool
for t in 0 5 10 15 20 30 40 50 60; do
  ffmpeg -y -ss $t -i full.mp4 -frames:v 1 -q:v 5 thumb_${t}s.jpg 2>/dev/null
done
ffmpeg -y -i full.mp4 -ss $START -to $END \
  -c:v libx264 -crf 15 -tune stillimage -pix_fmt yuv420p -an trimmed.mp4
```

## Narration

```python
SECTIONS = [{  # build-script input
    "id": "1a_feature_intro",
    "title": "Feature Name",  # title card
    "narration": 'Text. Use <break time="0.8s" /> for pauses.',
    "video": {"source": "recording-full.mp4", "trim": (start_sec, end_sec)},
}]
```

```python
from elevenlabs import ElevenLabs, VoiceSettings, save
client = ElevenLabs(api_key=key.strip())
audio = client.text_to_speech.convert(
    voice_id=VOICE_ID, text=section_text,
    model_id="eleven_turbo_v2_5",       # more natural than multilingual_v2 for English
    output_format="mp3_44100_192",      # Creator+ plan; 128 kbps sounds tinny; pcm_44100 needs Pro
    voice_settings=VoiceSettings(
        stability=0.75,                 # 0.6-0.8 for narration
        similarity_boost=0.85,
        style=0.0,                      # keep 0: fewer artifacts
        speed=0.92,                     # slightly slow for clarity
        use_speaker_boost=True),
    previous_text=prev[-200:], next_text=nxt[:200])  # continuity; omit for voice samples
save(audio, output_path)
```

ElevenLabs premade narration voices to sample: Roger `CwhRBWXzGAHq8TQ4Fs17`, Chris `iP95p4xoKVk53GoZ742B`, Eric `cjVigY5qzO86Huf0OWal`, Daniel `onwK4e9ZLuTAKqWW03F9`. Sample each on a representative 2-3 sentence text. Label each for the A/B video with the card command below.

The card command makes a labeled voice sample or a title card. Set its variables from the table.

```bash
ffmpeg -y -f lavfi -i "color=c=0x1a1a2e:size=${SIZE}:duration=${DUR}:rate=${RATE}" $AUDIO \
  -vf "drawtext=fontfile=${FONT}:text='${TEXT}':fontsize=${FS}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p" \
  -af "$AF" -c:v libx264 -crf ${CRF} -c:a aac -b:a 192k -ar 44100 -ac 2 $END "$OUT"
```

| Variable | Labeled voice sample | Title card |
| --- | --- | --- |
| `SIZE` | `1280x720` | `${W}x${H}` |
| `DUR` | `${dur}` | `3` |
| `RATE` | `24` | `10` |
| `AUDIO` | `-i sample.mp3` | `-f lavfi -i anullsrc=r=44100:cl=stereo` |
| `AF` | `volume=2.0,aformat=channel_layouts=stereo` | `anull` |
| `TEXT` | `${NAME}` | `Section Title` |
| `FS` | `48` | `52` |
| `CRF` | `18` | `15` |
| `END` | `-shortest` | `-t 3` |
| `OUT` | `labeled_sample.mp4` | `title.mp4` |

Convert the API's audio to stereo (`-ac 2`): the API returns mono, which plays silent on some devices. Boost it (`volume=2.0`) for the same reason.

Pronunciation aliases:

| Term | Spoken as |
| --- | --- |
| `jj` | `jay-jay` |
| `CLI` | `C L I` |
| `OAuth` | `Oh-Auth` |
| `uv sync` | `you-vee sync` |
| `tl run` | `T L run` |

## Normalize, title cards, concat

`scale` takes `W:H` with a colon, never `WxH`. `fps=10` is fine for a terminal.

Make each title card with the card command under "Narration", with its title-card values.

```bash
ffmpeg -y -i clip.mp4 \
  -vf "scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=0x1a1a2e,fps=10,format=yuv420p" \
  -c:v libx264 -crf 15 -c:a aac -b:a 192k -ar 44100 -ac 2 normalized.mp4
for clip in normalized_*.mp4; do echo "file '$clip'" >> concat.txt; done
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy final.mp4
```

## Quick PR demo (no narration)

Run the record and convert commands above on one cast. Then run `asciinema upload demo.cast` (preferred). Post the returned URL in the PR.
