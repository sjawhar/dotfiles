#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "lameenc"]
# ///
"""Render a two-speaker script to MP3 using Gemini 3.1 Flash TTS (Vertex AI REST).

Auth: uses `gcloud auth print-access-token` (no API key needed). Bills to the
gcloud-active project unless --project is given.

Script format: one line per turn, "Speaker: text". Two distinct speaker labels max.

  uv run tts.py --script script.txt --out episode.mp3 \
      --voices "Alex=Kore,Jordan=Puck"

The script is chunked so each TTS call stays under the model's audio-output cap;
PCM is concatenated and encoded to a single MP3.
"""
import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path

import lameenc
import requests

LOCATION = "global"
MODEL = "gemini-3.1-flash-tts-preview"
SAMPLE_RATE = 24000
CHUNK_CHARS = 1600  # keep each call well under the 16k audio-token output cap
STYLE = ("Read the following as a natural, engaging two-host podcast conversation. "
         "Keep it brisk and clear; do not add words that are not in the script.\n\n")


def token() -> str:
    return subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()


def active_project() -> str:
    return subprocess.check_output(
        ["gcloud", "config", "get-value", "project"], text=True).strip()


def parse_script(path: Path) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    cur_spk, cur_txt = None, []
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        m = re.match(r"^\s*([A-Za-z][\w .'-]{0,30}?):\s*(.*)$", line)
        if m:
            if cur_spk is not None:
                turns.append((cur_spk, " ".join(cur_txt).strip()))
            cur_spk, cur_txt = m.group(1).strip(), [m.group(2)]
        elif line.strip() and cur_spk is not None:
            cur_txt.append(line.strip())
    if cur_spk is not None:
        turns.append((cur_spk, " ".join(cur_txt).strip()))
    return [(s, t) for s, t in turns if t]


def chunk_turns(turns: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    chunks, cur, size = [], [], 0
    for spk, txt in turns:
        if cur and size + len(txt) > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append((spk, txt))
        size += len(txt)
    if cur:
        chunks.append(cur)
    return chunks


def synth(chunk: list[tuple[str, str]], voices: dict[str, str], proj: str, tok: str) -> bytes:
    text = STYLE + "\n".join(f"{s}: {t}" for s, t in chunk)
    speakers = [s for s in voices if any(spk == s for spk, _ in chunk)]
    speech: dict = {}
    if len(speakers) >= 2:
        speech["multiSpeakerVoiceConfig"] = {
            "speakerVoiceConfigs": [
                {"speaker": s, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voices[s]}}}
                for s in speakers
            ]
        }
    else:
        only = speakers[0] if speakers else next(iter(voices))
        speech["voiceConfig"] = {"prebuiltVoiceConfig": {"voiceName": voices[only]}}

    url = (f"https://aiplatform.googleapis.com/v1/projects/{proj}"
           f"/locations/{LOCATION}/publishers/google/models/{MODEL}:generateContent")
    body = {"contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech}}
    r = requests.post(url, headers={"Authorization": f"Bearer {tok}",
                                    "Content-Type": "application/json",
                                    "x-goog-user-project": proj}, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"TTS HTTP {r.status_code}: {r.text[:500]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    b64 = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
    return base64.b64decode(b64)


def to_mp3(pcm: bytes, out: Path, bitrate: int = 128) -> None:
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(SAMPLE_RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    out.write_bytes(enc.encode(pcm) + enc.flush())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voices", default="Alex=Kore,Jordan=Puck")
    ap.add_argument("--project", default=None)
    ap.add_argument("--bitrate", type=int, default=128)
    a = ap.parse_args()

    voices = dict(p.split("=", 1) for p in a.voices.split(","))
    turns = parse_script(Path(a.script))
    if not turns:
        print("No speaker turns parsed from script.", file=sys.stderr)
        return 1
    chunks = chunk_turns(turns)
    proj = a.project or active_project()
    tok = token()
    print(f"{len(turns)} turns -> {len(chunks)} TTS calls (project={proj}, voices={voices})")

    pcm = bytearray()
    for i, ch in enumerate(chunks, 1):
        audio = synth(ch, voices, proj, tok)
        pcm.extend(audio)
        print(f"  chunk {i}/{len(chunks)}: +{len(audio)} bytes")

    out = Path(a.out)
    to_mp3(bytes(pcm), out, a.bitrate)
    secs = len(pcm) / (SAMPLE_RATE * 2)
    print(f"wrote {out} — {out.stat().st_size} bytes, ~{secs/60:.1f} min audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
