"""Explainer videos: every `explainer*.mp4` artifact on a stream issue, with its script's title, its length and
upload time, served by the dashboard itself.

Dispatch cannot play them: its file route needs the machine's bearer token, answers with `Content-Disposition:
attachment`, and ignores Range requests, which iPhone Safari needs to play a video at all. So the refresh thread
downloads each video version once into the state directory, named by its sha256 (versions are immutable), and
`GET /video/<sha256>.mp4` serves it with Range support."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import urllib.parse
from pathlib import Path

from .sources import Dispatch, SourceError

VIDEO_NAME = re.compile(r"^(explainer(?:-[a-z0-9]+)*)\.mp4$", re.IGNORECASE)
SHA = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 16


def mp4_duration(path: Path) -> float | None:
    """Seconds, from the movie header (moov/mvhd) of an MP4 file; None when it cannot be read."""
    try:
        with path.open("rb") as f:
            size = path.stat().st_size
            moov = _find_box(f, 0, size, b"moov")
            if moov is None:
                return None
            mvhd = _find_box(f, moov[0], moov[1], b"mvhd")
            if mvhd is None:
                return None
            f.seek(mvhd[0])
            version = f.read(1)[0]
            f.read(3)  # flags
            if version == 1:
                f.read(16)  # creation and modification times
                timescale, duration = struct.unpack(">IQ", f.read(12))
            else:
                f.read(8)
                timescale, duration = struct.unpack(">II", f.read(8))
            return duration / timescale if timescale else None
    except (OSError, struct.error, IndexError):
        return None


def _find_box(f, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    """(payload start, payload end) of the first box of `kind` between `start` and `end`."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, typ = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return None
        if typ == kind:
            return pos + header, pos + size
        pos += size
    return None


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(first, last) byte of a single `bytes=` range, clipped to the file; None for no or an unusable header."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or (not m.group(1) and not m.group(2)) or size == 0:
        return None
    if not m.group(1):  # the last N bytes
        n = int(m.group(2))
        return (max(0, size - n), size - 1) if n else None
    first = int(m.group(1))
    last = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return (first, last) if first <= last else None


def front_matter_title(markdown: str) -> str | None:
    lines = (markdown or "").lstrip().splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        m = re.match(r"\s*title\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip("\"'") or None
    return None


def title_from_name(stem: str, stream_name: str) -> str:
    """explainer → "<stream> explainer"; explainer-nickolai-workflow → "Nickolai workflow"; a word with no vowel
    (lps) is an acronym and is capitalised."""
    words = stem.split("-")[1:]
    if not words:
        return f"{stream_name} explainer"
    words = [w.upper() if not re.search(r"[aeiouy]", w, re.IGNORECASE) else w for w in words]
    words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


class VideoStore:
    def __init__(self, root: Path | None = None) -> None:
        base = Path(os.environ.get("DASHBOARD_STATE_DIR", Path.home() / ".local" / "state" / "chief-dashboard"))
        self.root = root or base / "videos"
        self.root.mkdir(parents=True, exist_ok=True)
        self._durations: dict[str, float | None] = {}
        self._titles: dict[tuple[str, str, int], str | None] = {}

    def path(self, sha: str) -> Path | None:
        """The stored file for a sha256, or None (also for anything that is not a sha256)."""
        if not SHA.match(sha or ""):
            return None
        p = self.root / f"{sha}.mp4"
        return p if p.is_file() else None

    def fetch(self, api: Dispatch, key: str, slug: str, number: int, sha: str | None) -> tuple[str, float | None]:
        """Download one version once; (sha256, duration in seconds)."""
        if sha and self.path(sha):
            return sha, self.duration(sha)
        url = f"/api/v1/issues/{urllib.parse.quote(key)}/artifacts/{slug}/versions/{number}"
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".part")
        try:
            h = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, api.open(url) as r:
                while chunk := r.read(CHUNK):
                    h.update(chunk)
                    out.write(chunk)
            got = h.hexdigest()
            if sha and got != sha:
                raise SourceError(f"{key} {slug} v{number}: downloaded sha256 {got} is not the recorded {sha}")
            os.replace(tmp, self.root / f"{got}.mp4")
            return got, self.duration(got)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def duration(self, sha: str) -> float | None:
        if sha not in self._durations:
            p = self.path(sha)
            self._durations[sha] = mp4_duration(p) if p else None
        return self._durations[sha]

    def script_title(self, api: Dispatch, key: str, art: dict) -> str | None:
        number = max(v["number"] for v in art["versions"])
        ck = (key, art["slug"], number)
        if ck not in self._titles:
            base = f"/api/v1/issues/{urllib.parse.quote(key)}/artifacts/{art['slug']}"
            if art.get("kind") == "doc":
                text = api.get(f"{base}/text").get("markdown", "")
            else:
                with api.open(f"{base}/versions/{number}") as r:
                    text = r.read(200_000).decode(errors="replace")
            self._titles[ck] = front_matter_title(text)
        return self._titles[ck]

    def prune(self, keep: set[str]) -> None:
        """Delete stored videos no current explainer version uses."""
        for p in self.root.glob("*.mp4"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)

    def explainers(self, api: Dispatch, key: str, artifacts: list[dict], errors: list[str]) -> list[dict]:
        """The issue's explainer videos at their latest versions: {stem, name, uploaded, sha, seconds, title}."""
        by_name = {a.get("name", ""): a for a in artifacts}
        out = []
        for a in artifacts:
            m = VIDEO_NAME.match(a.get("name", ""))
            if not m or not a.get("versions"):
                continue
            v = max(a["versions"], key=lambda v: v["number"])
            row = {"stem": m.group(1).lower(), "name": a["name"], "uploaded": v.get("created_at"), "size": v.get("size")}
            try:
                row["sha"], row["seconds"] = self.fetch(api, key, a["slug"], v["number"], v.get("sha256"))
            except (SourceError, OSError) as e:
                errors.append(f"explainer {a['name']} on {key}: {e}")
                row["error"] = str(e)[:200]
            script = by_name.get(f"{m.group(1)}-script.md")
            if script and script.get("versions"):
                try:
                    row["title"] = self.script_title(api, key, script)
                except (SourceError, OSError) as e:
                    errors.append(f"explainer script {script['name']} on {key}: {e}")
            out.append(row)
        out.sort(key=lambda r: (r["stem"] != "explainer", r["stem"]))
        return out


def copy_range(src: Path, out, first: int, last: int) -> None:
    with src.open("rb") as f:
        f.seek(first)
        left = last - first + 1
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)

