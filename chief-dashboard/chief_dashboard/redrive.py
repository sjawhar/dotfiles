"""dispatch-check's re-drive: every row that waits on us gets an executor, one message and a time to answer by.

Why it sends instead of printing. On 2026-10-29 the 13:10Z board check found 15 asks Sami had answered that no
session had executed, the oldest 8 d 17 h. A person cleared them by hand at 13:23Z: every one was acted on or routed
in 13 minutes. The 17:10Z run printed the same 12 INFRA rows. A printed line holds nobody and sets no time, so the
count does not fall. Sami's order for changing agent behaviour is an instruction, then cross-review by another agent,
then a programmatic gate, and the next step only after a measurement shows the one before it failed. The hand clear
was the instruction step, and four hours later it had not held.

What it does with each WAITING-ON-US row:

1. **Names an executor**, in this order, and sends only to agent sessions, never to a person:
   the session that opened the ask; else the session that claims the issue; else the lead of the stream the issue
   belongs to; else the lead of the project's own stream (the session each scoreboard names). A candidate that is
   not alive is passed over. A row of a Legion tree, or one a Legion architect opened, takes only that project's
   Legion controller at the last step: an architect's claim outlives its process, so the tree is parked rather than
   gone, and the controller is the session that wakes it. When nothing resolves, the row prints as
   `DISPATCH-NO-EXECUTOR` with what was checked about each candidate it passed over. That row is for the chief to
   place by hand; it is never dropped and nothing is invented to send it to.
2. **Sets a time to acknowledge by** (`ACK_MINUTES`, default 60) and writes it to the ledger
   (`$DASHBOARD_STATE_DIR/redrive.json`), so the next run can tell an acknowledged row from a silent one.
3. **Sends one message** naming the ask, Sami's answer, how long the row has waited, and what an acknowledgement is:
   a reply in the ask's own thread saying it is received, what is being done, and by when.
4. **Reports the number that matters** on the next run: `DISPATCH-PAST-ACK`, one line per row whose time to
   acknowledge has passed with no reply, and its count in the summary. That is the number to drive to zero, not the
   raw waiting count. A row past its time is driven again, to the executor that resolves now.

A row disappears from WAITING-ON-US as soon as a session replies in its thread, cites it, or resolves it (that is
dispatch_check's own rule), so "the row is still here" IS "nobody has acknowledged it". A run that covers a row's
issue and no longer sees the row clears its ledger entry.

Liveness is never the registry alone. On 2026-10-29 a paused lane was restarted because a check could not tell
"paused" from "wedged". A session the Dispatch agent registry does not list is looked up in this box's tmux panes
and its pane is read for the pause screen: a paused session is alive, keeps the row, and is sent its message, which
waits for it. Nothing here calls a session dead and nothing here proposes restarting one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .sources import envoy_send

ACK_MINUTES = 60
STATE_DIR = Path(os.environ.get("DASHBOARD_STATE_DIR", Path.home() / ".local" / "state" / "chief-dashboard"))
# The pause screen omp draws over a paused session's pane.
PAUSE_SCREEN = re.compile(r"P\s?A\s?U\s?S\s?E\s?D")
# The titles the two Legion sessions carry: "Legion architect · INFRA-156" and "Legion controller · INFRA". An
# architect's claim outlives its process, so an architect Envoy does not list is parked, not gone; the controller
# of its project is the session that can wake its tree, and the one that triages that project's board.
LEGION_ARCHITECT = "Legion architect"
LEGION_CONTROLLER = "Legion controller"
SESSION_FILE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl")
# Removed from the lead roster on 2026-10-29 (/mnt/nvme/sami/chief/leads.json). A scoreboard that still names one of
# them names no lead: the check must not hand work to a session Sami has taken off the roster.
NOT_LEADS = frozenset({
    "01a13325-a08c-7572-acb7-c79d1750edd5",
    "01a1708b-35e2-746c-9dda-337c0544ec65",
    "01a15237-75c3-7321-befb-e6e1cc57a948",
    "01a14c3d-9d7f-7079-97aa-f810a884c050",
})


@dataclass
class Wait:
    """One row that waits on us: the finding's issue, the Dispatch node it is about, its printed text, how long it
    has waited, and the session that opened it when it is an ask."""

    key: str
    kind: str  # ask | comment | message
    node: str
    text: str
    waited: str
    author: dict | None = None
    question: str = ""
    answer: str = ""

    @property
    def rid(self) -> str:
        return f"{self.key}|{self.kind}|{self.node}"

    def ref(self, server: str) -> str:
        """Where the executor replies. A message has no page of its own, so its row points at the issue."""
        if self.kind == "message":
            return f"{server}/issues/{self.key}"
        return f"{server}/issues/{self.key}/{self.kind}s/{self.node}"


def _tmux(*args: str, timeout: float = 10) -> str:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, errors="replace",
                              timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split()]


def _omp_pid(root: int) -> int | None:
    """The `omp` process under a pane's process, at most three levels down (list_sessions.py in ~/src/scrum)."""
    frontier = [root]
    for _ in range(3):
        nxt: list[int] = []
        for p in frontier:
            for c in _children(p):
                try:
                    if Path(f"/proc/{c}/comm").read_text().strip() == "omp":
                        return c
                except OSError:
                    continue
                nxt.append(c)
        frontier = nxt
    return None


def _session_of(pid: int) -> str | None:
    """The session id of an omp process, from the transcript file it holds open."""
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                m = SESSION_FILE.search(os.readlink(f"/proc/{pid}/fd/{fd}"))
            except OSError:
                continue
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


def panes(wanted: set[str], tmux=_tmux) -> dict[str, str]:
    """{session id: pane} for the sessions of WANTED that run in a tmux pane on this box. The scan stops as soon as
    every wanted session is found, so the usual case costs a few panes, not all of them: reading one pane's session
    id walks its process tree, which is far too slow to do for every pane of the box on every row."""
    found: dict[str, str] = {}
    if not wanted:
        return found
    for line in tmux("list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}").splitlines():
        pane, _, ppid = line.partition("\t")
        if not ppid.strip().isdigit():
            continue
        omp = _omp_pid(int(ppid))
        sid = _session_of(omp) if omp else None
        if sid in wanted:
            found[sid] = pane
            if len(found) == len(wanted):
                break
    return found


class Liveness:
    """running | paused | elsewhere, for a session id.

    The Dispatch agent registry alone never decides. A session it does not list is looked up in this box's panes and
    its pane is read for the pause screen, so a paused lane is never taken for a gone one. `elsewhere` says only
    "not listening on Envoy and no pane here": it never says the session is dead, and nothing restarts it."""

    def __init__(self, live: dict[str, str], locate=None, tmux=_tmux) -> None:
        self.live = live
        self._tmux = tmux
        self._locate = locate or (lambda sid: panes({sid}, tmux=tmux).get(sid))
        self._state: dict[str, str] = {}

    def state(self, sid: str) -> str:
        if sid in self.live:
            return "running"
        if sid in self._state:
            return self._state[sid]
        pane = self._locate(sid)
        if pane is None:
            self._state[sid] = "elsewhere"
        else:
            screen = self._tmux("capture-pane", "-p", "-t", pane)
            self._state[sid] = "paused" if PAUSE_SCREEN.search(screen) else "running"
        return self._state[sid]


@dataclass
class Ledger:
    """What each row was driven to, and by when it must be acknowledged. One JSON file, read and written whole."""

    path: Path
    rows: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        rows = data.get("rows") if isinstance(data, dict) else None
        return cls(path, rows if isinstance(rows, dict) else {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"rows": self.rows}, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def live_row(self, rid: str) -> dict | None:
        """The row's open record, or None when it has none or its last one was cleared."""
        rec = self.rows.get(rid)
        return None if rec is None or rec.get("cleared_at") else rec

    def clear(self, rid: str, when: str) -> None:
        rec = self.live_row(rid)
        if rec is not None:
            rec["cleared_at"] = when


def iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _minutes(delta: dt.timedelta) -> str:
    m = int(delta.total_seconds() // 60)
    return f"{m // 60}h {m % 60}m" if abs(m) >= 60 else f"{m}m"


def message(w: Wait, server: str, due: dt.datetime, now: dt.datetime, attempt: int, why: str) -> str:
    """The one message the executor gets: the ask, Sami's answer, how long the row has waited, and what an
    acknowledgement is. ASD-STE100: short sentences, one instruction in each."""
    head = (f"Sami answered this {w.waited} ago and no session has acted on it:" if w.answer else
            f"This has waited {w.waited} for an agent. No session has acted on it:")
    lines = [
        "From the chief of staff's board check. This is not a status request.",
        "",
        head,
        f"  {w.ref(server)}",
    ]
    if w.answer:
        lines.append(f'  His answer: "{w.answer}"')
    if w.question:
        lines.append(f"  It was asked: {w.question[:400]}")
    lines += [
        "",
        f"You are the executor because {why}.",
        "",
        f"Acknowledge by {iso(due)}, in {_minutes(due - now)}.",
        "To acknowledge, write one reply in that thread with three things:",
        "  1. that you have it,",
        "  2. what you do about it,",
        "  3. when it is done.",
        "",
        "Then do the work. If the work is not yours, name in the same thread the session or the issue that holds it.",
        "The next board check reports each row that has no reply after its time. Silence keeps it on that list.",
    ]
    if attempt > 1:
        lines.append(f"This is drive {attempt} of this row. Drive {attempt - 1} got no reply in its thread.")
    return "\n".join(lines)


class Driver:
    """Re-drives the rows a run found: names an executor, sends, records the time to acknowledge by, and reports the
    rows whose time has passed."""

    def __init__(self, ledger: Ledger, send=envoy_send, ack_minutes: int = ACK_MINUTES, dry_run: bool = False,
                 tmux=_tmux, locate=None) -> None:
        self.ledger = ledger
        self.send = send
        self.ack = dt.timedelta(minutes=ack_minutes)
        self.dry_run = dry_run
        self.tmux = tmux
        self.locate = locate
        self.sent: list[tuple[str, str, str]] = []  # (row id, session, message), for the dry run and the tests

    # ------------------------------------------------------------------------------------------ the executor
    def candidates(self, w: Wait, data: dict, project: str) -> list[tuple[str, str, str]]:
        """(session id, name, why it is the executor), in the order the rule gives: the session that opened the ask,
        the issue's claim holder, the lead of the stream the issue belongs to, and last the lead of the project's
        own stream. A claim a person holds is left out: executors are agent sessions."""
        out: list[tuple[str, str, str]] = []
        live, meta = data["live"], data["meta"]
        name = lambda a: a.get("origin", {}).get("session_title") or live.get(a["id"]) or a["id"][:8]  # noqa: E731
        add = lambda sid, label, why: (  # noqa: E731
            out.append((sid, label or sid[:8], why))
            if sid and sid not in NOT_LEADS and not any(c[0] == sid for c in out) else None)
        if w.author and w.author.get("kind") == "session":
            add(w.author["id"], name(w.author), "you opened this ask")
        claim = ((meta.get(w.key) or {}).get("claim") or {}).get("actor") or {}
        if claim.get("kind") == "session":
            add(claim["id"], name(claim), f"you claim {w.key}")
        seen: set[str] = set()
        k: str | None = w.key
        while k and k not in seen:
            seen.add(k)
            if "stream" in (meta.get(k) or {}).get("labels", []) or k in data["streams"]:
                lead = (data["streams"].get(k) or {}).get("lead") or {}
                add(lead.get("session"), lead.get("title"), f"you lead stream {k}, which {w.key} belongs to")
                break
            k = (meta.get(k) or {}).get("parent")
        legion = self.legion(w, data, out)
        for sid, title, key in self.project_leads(data, project):
            if legion and not title.startswith(LEGION_CONTROLLER):
                continue  # a parked Legion tree is woken by its controller, not by another stream's lead
            add(sid, title, f"you lead {project}'s stream {key} and nothing nearer the issue can take this")
        return out

    @staticmethod
    def project_leads(data: dict, project: str) -> list[tuple[str, str, str]]:
        """(session, title, stream key) of each lead the project's own stream issues name, without duplicates."""
        found: dict[str, tuple[str, str, str]] = {}
        for key, st in sorted(data["streams"].items()):
            if (st.get("project") or key.rsplit("-", 1)[0]) != project:
                continue
            lead = st.get("lead") or {}
            if lead.get("session") and lead["session"] not in found:
                found[lead["session"]] = (lead["session"], lead.get("title") or lead["session"][:8], key)
        return list(found.values())

    @staticmethod
    def legion(w: Wait, data: dict, passed: list[tuple[str, str, str]]) -> bool:
        """Whether the row belongs to a Legion tree: its issue or an ancestor carries the `legion` label, or a
        Legion architect opened it. Such a tree is parked, not gone — a Legion claim outlives its process by design
        — and its controller is the one session that can wake it (it also triages that project's board)."""
        if any(label.startswith(LEGION_ARCHITECT) for _, label, _ in passed):
            return True
        seen: set[str] = set()
        k: str | None = w.key
        while k and k not in seen:
            seen.add(k)
            m = data["meta"].get(k) or {}
            if "legion" in (m.get("labels") or []):
                return True
            k = m.get("parent")
        return False

    def executor(self, w: Wait, data: dict, liveness: Liveness,
                 project: str) -> tuple[tuple[str, str, str] | None, str]:
        """The executor to send to, and the reason line. A running or paused session is alive and takes the row; a
        session that is neither is passed over, and the reason says exactly what was checked about it."""
        passed = []
        for sid, label, why in self.candidates(w, data, project):
            state = liveness.state(sid)
            if state in ("running", "paused"):
                return (sid, label, why), state
            passed.append(f"{label} ({sid[:8]}) is not listening on Envoy and has no pane on this box")
        if not passed:
            passed.append(f"no session opened it, no session claims the issue, and no stream of {project} names a "
                          "lead")
        return None, "; ".join(passed)

    # ------------------------------------------------------------------------------------------ the run
    def __call__(self, out: dict[str, list], waits: list[Wait], data: dict, now: dt.datetime, project: str = "",
                 owner_of=None, owner: str | None = None) -> None:
        """Drives each row of WAITS, rewrites the WAITING-ON-US findings with what it did, and appends the PAST-ACK
        and NO-EXECUTOR findings. OWNER keeps a lead's run to its own rows."""
        liveness = Liveness(data["live"], locate=self.locate, tmux=self.tmux)
        server, lines = data["server"], []
        for w in waits:
            if owner and owner_of is not None and not (owner_of(w.key, data)[1] or "").startswith(owner):
                lines.append((w.key, w.text))
                continue
            lines.append((w.key, w.text + " | " + self._drive(w, out, data, liveness, server, now, project)))
        out["WAITING-ON-US"] = lines
        self._clear_gone(waits, data, now)
        if not self.dry_run:
            self.ledger.save()

    def _drive(self, w: Wait, out: dict[str, list], data: dict, liveness: Liveness, server: str,
               now: dt.datetime, project: str) -> str:
        rec = self.ledger.live_row(w.rid)
        due = dt.datetime.fromisoformat(rec["due"].rstrip("Z")).replace(tzinfo=dt.UTC) if rec and rec.get("due") \
            else None
        if rec is not None and due is not None and now <= due:
            at = rec["attempts"][-1]["at"]
            return (f"drive: {rec['executor_name']} ({rec['executor'][:8]}) was sent this {at[11:16]}Z, "
                    f"acknowledgement due {due:%H:%M}Z, in {_minutes(due - now)}")
        if rec is not None and due is not None:
            out["PAST-ACK"].append((w.key, f"{w.key} {w.kind} {w.node[:8]}: no reply in its thread "
                                           f"{_minutes(now - due)} after the time set ({iso(due)}); "
                                           f"{len(rec['attempts'])} drive(s) since {rec['first_seen']}; "
                                           f"{w.text[:90]}"))
        found, why = self.executor(w, data, liveness, project)
        if found is None:
            self._record(w, None, None, now, why)
            out["NO-EXECUTOR"].append((w.key, f"{w.key} {w.kind} {w.node[:8]} has waited {w.waited} and nothing can "
                                              f"take it: {why}. Place it by hand ({w.ref(server)}); {w.text[:90]}"))
            return f"drive: NO EXECUTOR ({why})"
        sid, label, reason = found
        attempt = len(rec["attempts"]) + 1 if rec else 1
        new_due = now + self.ack
        text = message(w, server, new_due, now, attempt, reason)
        state = liveness.state(sid)
        delivered, err = self._send(w, sid, text, attempt)
        self._record(w, found, new_due, now, why="", delivered=delivered)
        where = "" if state == "running" else f", {state}: the message waits for it"
        how = "would send" if self.dry_run else ("sent" if delivered else f"NOT DELIVERED ({err})")
        return (f"drive {attempt}: {label} ({sid[:8]}){where}; {how}; acknowledge by {iso(new_due)}")

    def _send(self, w: Wait, sid: str, text: str, attempt: int) -> tuple[bool, str]:
        self.sent.append((w.rid, sid, text))
        if self.dry_run:
            return True, ""
        try:
            env = self.send(sid, text, idempotency_key=f"redrive:{w.rid}:{attempt}", source="agent")
        except Exception as e:  # noqa: BLE001 - a hub failure is reported on the row, the rest of the run still goes
            return False, " ".join(str(e).split())[:120]
        if env.get("recipient"):
            return True, ""
        return False, "no listener on Envoy now"

    def _record(self, w: Wait, found: tuple[str, str, str] | None, due: dt.datetime | None, now: dt.datetime,
                why: str, delivered: bool = False) -> None:
        rec = self.ledger.live_row(w.rid)
        if rec is None:
            rec = {"issue": w.key, "kind": w.kind, "node": w.node, "first_seen": iso(now), "attempts": [],
                   "id": str(uuid.uuid4())}
            self.ledger.rows[w.rid] = rec
        rec["cleared_at"] = None
        rec["waited"] = w.waited
        if found is None:
            rec["due"], rec["executor"], rec["executor_name"], rec["no_executor"] = None, None, None, why
            return
        sid, label, _ = found
        rec.pop("no_executor", None)
        rec["due"], rec["executor"], rec["executor_name"] = iso(due) if due else None, sid, label
        rec["attempts"].append({"at": iso(now), "executor": sid, "name": label, "due": iso(due) if due else None,
                                "delivered": delivered})

    def _clear_gone(self, waits: list[Wait], data: dict, now: dt.datetime) -> None:
        """A row this run covered and no longer sees was acknowledged (a session replied, cited or resolved it, which
        is what takes it off the list); its record closes so a later recurrence starts a fresh drive."""
        here = {w.rid for w in waits}
        covered = set(data["reads"])
        for rid, rec in self.ledger.rows.items():
            if rid not in here and rec.get("issue") in covered and not rec.get("cleared_at"):
                self.ledger.clear(rid, iso(now))
