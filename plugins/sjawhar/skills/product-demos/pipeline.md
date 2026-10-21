# Pipeline commands

## Setup

```bash
cargo install --git https://github.com/asciinema/agg   # not `cargo install agg`: that is a different crate
uv venv /tmp/demo/venv && source /tmp/demo/venv/bin/activate
uv pip install elevenlabs
which agg ffmpeg ffprobe
```

## Record, convert, trim

```bash
asciinema rec --cols 120 --rows 35 /tmp/demo/recordings/<section>.cast
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

Sample voices: Roger `CwhRBWXzGAHq8TQ4Fs17`, Chris `iP95p4xoKVk53GoZ742B`, Eric `cjVigY5qzO86Huf0OWal`, Daniel `onwK4e9ZLuTAKqWW03F9`, on a representative 2-3 sentence text. Label each for the A/B video:

```bash
ffmpeg -y -f lavfi -i "color=c=0x1a1a2e:size=1280x720:duration=${dur}:rate=24" -i sample.mp3 \
  -filter_complex "[1:a]volume=2.0,aformat=channel_layouts=stereo[a];
    [0:v]drawtext=fontfile=${FONT}:text='${NAME}':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p[v]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac -b:a 192k -ar 44100 -ac 2 -shortest labeled_sample.mp4
```

The API returns mono, which plays silent on some devices: convert to stereo (`-ac 2`) and boost (`volume=2.0`).

Pronunciation aliases: `jj` → `jay-jay`, `CLI` → `C L I`, `OAuth` → `Oh-Auth`, `uv sync` → `you-vee sync`, `tl run` → `T L run`.

## Normalize, title cards, concat

`scale` takes `W:H` with a colon, never `WxH`. `fps=10` is fine for a terminal.

```bash
ffmpeg -y -i clip.mp4 \
  -vf "scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=0x1a1a2e,fps=10,format=yuv420p" \
  -c:v libx264 -crf 15 -c:a aac -b:a 192k -ar 44100 -ac 2 normalized.mp4
ffmpeg -y -f lavfi -i "color=c=0x1a1a2e:size=${W}x${H}:duration=3:rate=10" -f lavfi -i "anullsrc=r=44100:cl=stereo" \
  -vf "drawtext=fontfile=${FONT}:text='Section Title':fontsize=52:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p" \
  -c:v libx264 -crf 15 -c:a aac -b:a 192k -t 3 title.mp4
for clip in normalized_*.mp4; do echo "file '$clip'" >> concat.txt; done
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy final.mp4
```

## Quick PR demo (no narration)

Run the record and convert commands above on one cast. Then `asciinema upload demo.cast` (preferred) and post the returned URL in the PR.
