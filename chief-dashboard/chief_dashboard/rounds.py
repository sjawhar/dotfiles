"""The learning loop's newest round (/mnt/nvme/sami/chief/learning-loop.md): the changes the chief of staff sent to other
streams with their before and after numbers, which of them each lead adopted, and the leads that missed the round.

Read from the round files the chief of staff writes (`CHIEF_ROUNDS`, default /mnt/nvme/sami/chief/rounds/):

- `<UTC date>T<HH>.md`, the round. Its header line `Missed: ...` names the leads that sent no input. Its last
  section, a heading starting `# FINAL`, lists the changes sent as bullets `* A. <text> -> LEAD, LEAD.` (the text
  carries the before and after numbers); other bullets of that section (DROPPED, coaching, procedure) are kept as
  notes. With no FINAL section yet, the round shows as not final.
- `<same stem>-adoptions.md`, the leads' replies: one bullet per reply, `* LEAD HH:MMZ: A ADOPT (...). B NOT ADOPT
  (...)`. A letter or number followed by ADOPT or NOT ADOPT is that lead's answer to that item.

Every part is optional: a missing file, section or line leaves that part empty, never an error."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROUNDS = Path(os.environ.get("CHIEF_ROUNDS", "/mnt/nvme/sami/chief/rounds"))
ROUND_NAME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d\.md$")
FILE_NAME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d(?:-[a-z]+)?\.md$")  # the files /rounds/<name> may serve

_BULLET = re.compile(r"^\s*[*-]\s+(.*)$")
_CHANGE = re.compile(r"^([A-Z])\.\s+(.*)$")
_ANSWER = re.compile(r"(?<![\w-])([A-Z]|\d{1,2})\s+(NOT ADOPT|ADOPT)\b")
_REPLY = re.compile(r"^(.+?)\s+(\d\d:\d\dZ)\b[^:]*:\s*(.*)$")


@dataclass
class Change:
    key: str  # "A"
    text: str  # what to copy, with its before and after numbers
    to: list[str] = field(default_factory=list)  # the leads it was sent to


@dataclass
class Reply:
    lead: str
    at: str
    answers: dict[str, str]  # item key -> "ADOPT" or "NOT ADOPT"
    text: str


@dataclass
class Round:
    name: str  # the round file's name, e.g. 2026-10-26T03.md
    title: str
    missed: str | None  # the round's own words on who missed it; None when it does not say
    final: bool
    final_title: str | None = None
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)
    adoptions_name: str | None = None
    inputs_name: str | None = None
    redteam_name: str | None = None

    def answer(self, lead: str, key: str) -> str | None:
        for r in self.replies:
            if r.lead == lead and key in r.answers:
                return r.answers[key]
        return None

    @property
    def leads(self) -> list[str]:
        """Every lead a change went to or that replied, in first-seen order."""
        out: list[str] = []
        for name in [t for c in self.changes for t in c.to] + [r.lead for r in self.replies]:
            if name not in out:
                out.append(name)
        return out

    def silent(self) -> list[str]:
        """Leads a change was sent to that have not replied."""
        replied = {r.lead for r in self.replies}
        return [n for n in dict.fromkeys(t for c in self.changes for t in c.to) if n not in replied]


def newest(root: Path | None = None) -> Path | None:
    root = root or ROUNDS
    try:
        names = sorted(p.name for p in root.iterdir() if ROUND_NAME.match(p.name))
    except OSError:
        return None
    return root / names[-1] if names else None


def recipients(text: str) -> tuple[str, list[str]]:
    """(the change's text, the leads after its last spaced arrow): "ORCH 4->1 ... -> TASKCOACH, FRAC. (Not INFRA: ...)".
    Numbers like "4->1" carry no spaces, so only the arrow before the leads splits; words after the leads stay."""
    head, arrow, tail = text.rpartition(" -> ")
    if not arrow:
        return text.strip(), []
    m = re.match(r"([^.(]*)[.]?\s*(.*)$", tail)
    names, rest = m.group(1), m.group(2).strip()
    return (head.strip() + (f" {rest}" if rest else "")), [n.strip() for n in names.split(",") if n.strip()]


def parse_round(text: str, name: str) -> Round:
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), name)
    missed = next((ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("missed:")), None)
    start = next((i for i in range(len(lines) - 1, -1, -1) if re.match(r"^#+\s*FINAL\b", lines[i])), None)
    rnd = Round(name, title, missed, start is not None)
    if start is None:
        return rnd
    rnd.final_title = lines[start].lstrip("# ").strip()
    for ln in lines[start + 1:]:
        m = _BULLET.match(ln)
        if not m:
            continue
        body = m.group(1).strip()
        c = _CHANGE.match(body)
        if c:
            text_, to = recipients(c.group(2))
            rnd.changes.append(Change(c.group(1), text_, to))
        else:
            rnd.notes.append(body)
    return rnd


def parse_adoptions(text: str) -> list[Reply]:
    out = []
    for ln in text.splitlines():
        m = _BULLET.match(ln)
        r = _REPLY.match(m.group(1)) if m else None
        if not r:
            continue
        answers: dict[str, str] = {}
        for key, verdict in _ANSWER.findall(r.group(3)):
            answers.setdefault(key, verdict)
        out.append(Reply(r.group(1).strip(), r.group(2), answers, r.group(3).strip()))
    return out


def load(root: Path | None = None) -> Round | None:
    root = root or ROUNDS
    path = newest(root)
    if path is None:
        return None
    try:
        rnd = parse_round(path.read_text(), path.name)
    except OSError:
        return None
    stem = path.name.removesuffix(".md")
    adoptions = root / f"{stem}-adoptions.md"
    try:
        rnd.replies = parse_adoptions(adoptions.read_text())
        rnd.adoptions_name = adoptions.name
    except OSError:
        pass
    for kind in ("inputs", "redteam"):
        if (root / f"{stem}-{kind}.md").exists():
            setattr(rnd, f"{kind}_name", f"{stem}-{kind}.md")
    return rnd


def watched(root: Path | None = None) -> list[Path]:
    """The files whose change changes the section: the folder (a new round) and the newest round's files."""
    root = root or ROUNDS
    path = newest(root)
    if path is None:
        return [root]
    stem = path.name.removesuffix(".md")
    return [root, path, root / f"{stem}-adoptions.md"]
