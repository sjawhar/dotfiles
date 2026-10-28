#!/usr/bin/env python3
"""scripts/sami-said: what a person typed into omp sessions, and nothing omp or an agent injected
into the user role. Fixtures are an omp agent directory shaped as omp writes it: session files
(sessions/<cwd>/<time>_<id>.jsonl, line 1 a padded title entry, line 2 the session header, then
entries with a top-level ISO timestamp), the terminal's prompt history (history.db, the `history`
table omp creates), a subagent file below a session, and a shake log. Entry shapes are copied from
real sessions of 2026-10-27/28; ids and texts are invented. --dispatch runs against a local fake of
Dispatch's routes: /api/v1/issues, /api/v1/issues/<key>/events and the /api/v1/events SSE log.
"""

from __future__ import annotations

import http.server
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "sami-said"
UTC = timezone.utc
T0 = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
WINDOW = ["--since", "2026-10-28T01:00Z", "--until", "2026-10-28T03:00Z"]
LEAD = "01a15f9c-6bee-7127-b4f1-907ec8d3176d"
HEADLESS = "01a17f2f-e8e2-70d2-94e4-a9e53604d87b"
OLD = "01a14ba2-817c-748e-9cb5-d1ad60308a9c"
LANE = "01a17652-fea6-71e0-843b-0d307be1fe25"
FORK = "01a17147-4a9e-758d-bfaa-58bac7006089"


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def stamp(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def user(t: datetime, text: str, **extra) -> dict:
    message = {"role": "user", "content": [{"type": "text", "text": text}], "attribution": "user",
               "timestamp": int(t.timestamp() * 1000), **extra}
    return {"type": "message", "id": "u1", "parentId": "p", "timestamp": stamp(t), "message": message}


def custom(t: datetime, ctype: str, content: str, **extra) -> dict:
    return {"type": "custom_message", "customType": ctype, "content": content, "display": True,
            "attribution": "agent", "id": "c1", "parentId": "p", "timestamp": stamp(t), **extra}


def assistant(t: datetime, text: str) -> dict:
    return {"type": "message", "id": "a1", "parentId": "p", "timestamp": stamp(t),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def line(e: dict) -> str:
    """One entry as omp writes it: compact JSON, non-ASCII kept."""
    return json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n"


def write_session(path: Path, sid: str, cwd: str, title: str, entries: list[dict], **header) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = [{"type": "title", "v": 1, "title": title, "source": "user", "pad": " " * 40},
            {"type": "session", "version": 3, "id": sid, "timestamp": stamp(at(-600)), "cwd": cwd, **header}]
    path.write_text("".join(line(e) for e in head + entries))
    return path


def write_history(root: Path, rows: list[tuple[str, datetime, str]]) -> None:
    con = sqlite3.connect(root / "history.db")
    con.execute("CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT NOT NULL UNIQUE, "
                "created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)), cwd TEXT, "
                "session_id TEXT, use_count INTEGER NOT NULL DEFAULT 1)")
    for prompt, t, sid in rows:
        con.execute("INSERT INTO history (prompt, created_at, cwd, session_id) VALUES (?, ?, ?, ?)",
                    (prompt, int(t.timestamp()), "/home/sami/src/machcsl-riscv", sid))
    con.commit()
    con.close()


GUIDED = "/guided-goal Get the pilot running on one region, then run it."
TYPED = "Why are we not doing the thing that Raj asked for?"
STEER = "Can't you run things on a different VM?"
LONG_TYPED = "Whatever we build has to be event-based. Go Rust, strongly typed."
SKILL_ARGS = "I'm thinking we should write down our strategy"
AGENT_NOTE = "From the chief of staff, for Sami: this typed message only clears an omp latch."
DRAFT = "i honestly have no idea what this team is working on"
SENT = DRAFT + "\n\nand i dont see why you need 4 agents working on it"
AFTER_FORK = "Keep the fork on the pilot only."
KICKED = "01a1711d-0caf-7740-9b2b-f69dcfeab801"
KICK_RENAME = "/rename 🧹 S1: delete unused grader settings"
KICKOFF = (f'<file name="/mnt/nvme/sami/frac-verify/peers/S1-kickoff.md">\nYou lead slice S1 of the grader stream. '
           f"Set your title first: `tmux send-keys -t \"$TMUX_PANE\" -l '{KICK_RENAME}' && tmux send-keys -t \"$TMUX_PANE\" Enter`. "
           "Then plan S1 under SDD and implement it, and report to the lead over Envoy.\n</file>\n")
ASKED = "https://dispatch.example/issues/INFRA-228/asks/c8943356 please help investigate and fix this."
COACH = "01a16a9e-a90d-7148-95ed-0e9e7839dbd2"
RESUME_NOTE = "From TASKCOACH: you are resumed on Sami's word. Wrap up only, then stop and wait."
PAUSE = "PAUSE-TOGGLE (person or scripted Escape; history.db records the resume time)"
NUDGE = "From TASKCOACH: check your Envoy inbox when you are resumed, nothing else."


class Store:
    """One agent directory holding every case the rules decide."""

    def __init__(self, root: Path):
        self.root = root
        sessions = root / "sessions"
        lead_dir = sessions / "-src-machcsl-riscv"
        self.lead = write_session(lead_dir / f"2026-10-21T17-57-29-198Z_{LEAD}.jsonl", LEAD, "/home/sami/src/machcsl-riscv",
                                  "🎼 ORCH: orchestration lead", [
            user(at(-90), "typed before the window"),
            user(at(1), TYPED),
            user(at(2), TYPED, attribution="agent"),  # a parent relaying Sami's exact words
            user(at(3), "Continue.", synthetic=True),  # the automatic continue, worded as Sami once typed
            user(at(4), STEER, steering=True),
            user(at(5), "I'm getting super excited.", tag="407cb29c-fcc3-406e-8fd8-ecc6d9076ba4"),
            user(at(6), "Someone else's Dispatch message.", tag="99999999-0000-4000-8000-000000000000"),
            user(at(7), "[shaken ~40 tokens — recover: artifact://7 (region 3)]"),
            user(at(8), AGENT_NOTE),
            user(at(9), SENT),
            custom(at(10), "envoy-message", "envoy:\n  to: you\n  from: dispatch\n  dispatch:\n    actor:\n      kind: user\n"),
            custom(at(11), "irc:incoming", "<irc>\nIncoming IRC message from agent `RevertToUpstream`"),
            custom(at(12), "async-result", "<system-notice>\nBackground job bg_1 has completed."),
            custom(at(13), "post-compaction-reminder", "<system-reminder>\nContext was just compacted."),
            custom(at(14), "skill-prompt", "[IMPORTANT: User invoked the \"ce-strategy\" skill]\n# Product Strategy",
                   attribution="user", details={"name": "ce-strategy", "path": "/x/SKILL.md", "args": SKILL_ARGS}),
            {"type": "compaction", "id": "k", "parentId": "p", "timestamp": stamp(at(15)), "summary": "## Goal\nLead the stream"},
            {"type": "message", "id": "d1", "parentId": "p", "timestamp": stamp(at(16)),
             "message": {"role": "developer", "attribution": "agent", "synthetic": True,
                         "content": [{"type": "text", "text": "`/guided-goal`: goal mode\n<rough-goal>\nGet the pilot running"}]}},
            assistant(at(17), "I will do it."),
            user(at(49.97), NUDGE),  # an agent typed into LEAD's pane, but pressed no Escape there
        ])
        shake = lead_dir / f"2026-10-21T17-57-29-198Z_{LEAD}" / "7.shake.log"
        shake.parent.mkdir(parents=True)
        shake.write_text(f"### region 3 (user)\n{LONG_TYPED}\n### region 4 (tool)\nother\n")
        # A scripted session: its launch prompt carries attribution "user" but was never typed.
        write_session(sessions / "--mnt-nvme-sami-tmp-xreview--" / f"2026-10-28T02-00-00-000Z_{HEADLESS}.jsonl", HEADLESS,
                      "/mnt/nvme/sami/tmp/xreview", "", [user(at(20), "You are a standing part architect in a large Lean proof graph.")])
        # A file last written before the window is never opened, even with an entry stamped inside it.
        old = write_session(sessions / "--mnt-nvme-sami-librarian--" / f"2026-10-17T20-51-43-612Z_{OLD}.jsonl", OLD,
                            "/mnt/nvme/sami/librarian", "📚 LIBRAR",
                            [user(at(25), "stale but stamped in the window", tag="0d1b53ba-0000-4000-8000-000000000001")])
        os.utime(old, (at(-120).timestamp(), at(-120).timestamp()))
        # A subagent someone typed into: below its parent's directory, named by agent, not by id.
        lane = lead_dir / f"2026-10-21T17-57-29-198Z_{LEAD}" / "GuardLane.jsonl"
        lane.write_text(line({"type": "session", "version": 3, "id": LANE, "timestamp": stamp(at(-60)),
                              "cwd": "/home/sami/Code/personal/theorem", "title": "🛡️ GuardLane"})
                        + line(user(at(30), "Your brief: check the guard."))
                        + line(user(at(31), "why do you need 4 agents on this?")))
        # A fork of the lead: omp copies the lead's entries, with their own times, into the new file.
        write_session(lead_dir / f"2026-10-28T02-35-00-000Z_{FORK}.jsonl", FORK, "/home/sami/src/machcsl-riscv",
                      "🎼 ORCH: orchestration lead", [
            user(at(1), TYPED),
            user(at(5), "I'm getting super excited.", tag="407cb29c-fcc3-406e-8fd8-ecc6d9076ba4"),
            user(at(36), AFTER_FORK),
        ], timestamp=stamp(at(35)), parentSession=LEAD)
        write_history(root, [
            ("typed before the window", at(-90), LEAD),
            ("Continue.", at(-200), LEAD),
            (TYPED, at(1), LEAD),
            (STEER, at(4), LEAD),
            (LONG_TYPED, at(7), LEAD),
            (AGENT_NOTE, at(8), LEAD),
            (DRAFT, at(8.5), LEAD),
            (SENT, at(9), LEAD),
            (f"/skill:ce-strategy {SKILL_ARGS}", at(14), LEAD),
            (GUIDED, at(16), LEAD),
            ("/btw what are you working on?", at(40), LEAD),
            ("From the coach, from the transcript audit (dispatch://INFRA-167): LANE-E has no issue.", at(41), LEAD),
            ("why do you need 4 agents on this?", at(31), LANE),
            (AFTER_FORK, at(36), FORK),
            (KICK_RENAME, at(45.2), KICKED),
            (ASKED, at(44), KICKED),
            # omp writes /pause when the pause screen closes. Here an agent's script pressed Escape and
            # typed into another session's pane, so nothing ties that Escape to LEAD: the row stays.
            ("/pause", at(50), LEAD),
            (RESUME_NOTE, at(49.95), KICKED),
            (NUDGE, at(49.97), LEAD),
        ])
        # A session an agent started with `omp @kickoff.md`: the kickoff is in no history row, but holds
        # the /rename line the session then typed into its own pane. Then a person re-sends a prompt
        # with a skill in front of it, which only the containment match finds.
        write_session(sessions / "-Code-personal-theorem" / f"2026-10-28T02-44-00-000Z_{KICKED}.jsonl", KICKED,
                      "/home/sami/Code/personal/theorem", "🧹 S1", [
            user(at(45), KICKOFF),
            tool_call(at(45.1), "bash", {"command": f"tmux send-keys -t \"$TMUX_PANE\" -l '{KICK_RENAME}' && tmux send-keys -t \"$TMUX_PANE\" Enter"}),
            user(at(46), f"/skill:systematic-debugging {ASKED}"),
            user(at(49.95), RESUME_NOTE),
        ])
        write_session(sessions / "-src-binary-equivalence-taiga" / f"2026-10-23T21-15-45-293Z_{COACH}.jsonl", COACH,
                      "/home/sami/src/binary-equivalence-taiga", "🏋️ TASKCOACH", [
            tool_call(at(49.9), "bash", {"command": f"tmux send-keys -t %29 Escape; sleep 3; tmux send-keys -t %29 -l "
                                                    f"{json.dumps(RESUME_NOTE)}; tmux send-keys -t %29 Enter"}),
            tool_call(at(49.92), "bash", {"command": f"tmux send-keys -t %31 -l {json.dumps(NUDGE)}; tmux send-keys -t %31 Enter"}),
        ])


def run(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), "--agent-dir", str(root), *WINDOW, *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=60)


def rows(root: Path, *args: str, env: dict | None = None) -> list[dict]:
    done = run(root, "--json", *args, env=env)
    assert done.returncode == 0, done.stderr
    return [json.loads(line) for line in done.stdout.splitlines()]


class TranscriptRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "agent"
        Store(cls.root)
        cls.rows = rows(cls.root)
        cls.texts = [r["text"] for r in cls.rows]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_typed_messages_come_out_oldest_first_with_session_details(self):
        first = self.rows[0]
        self.assertEqual(first["text"], TYPED)
        self.assertEqual(first["source"], "terminal")
        self.assertEqual(first["time"], "2026-10-28T02:01:00Z")
        self.assertEqual(first["session"], LEAD)
        self.assertEqual(first["title"], "🎼 ORCH: orchestration lead")
        self.assertEqual(first["cwd"], "/home/sami/src/machcsl-riscv")
        self.assertEqual([r["time"] for r in self.rows], sorted(r["time"] for r in self.rows))

    def test_exactly_the_typed_messages_each_from_its_source(self):
        self.assertEqual([(r["text"], r["source"]) for r in self.rows], [
            (TYPED, "terminal"),
            (STEER, "terminal"),
            ("I'm getting super excited.", "dispatch-turn"),
            ("Someone else's Dispatch message.", "dispatch-turn"),  # without --dispatch its author is not checked
            (LONG_TYPED, "terminal"),
            (SENT, "terminal"),
            (f"/skill:ce-strategy {SKILL_ARGS}", "terminal"),  # from the skill-prompt entry, not the history fallback
            (GUIDED, "terminal-history"),
            ("why do you need 4 agents on this?", "terminal"),
            (AFTER_FORK, "terminal"),
            ("/btw what are you working on?", "terminal-history"),
            (f"/skill:systematic-debugging {ASKED}", "terminal"),  # found by containment: ASKED is not shown again
            ("/pause", PAUSE),
        ])

    def test_injected_user_role_text_is_left_out(self):
        joined = "\n".join(self.texts)
        for injected in ("Continue.", "envoy:", "<irc>", "Background job", "Context was just compacted",
                         "Product Strategy", "## Goal", "goal mode", "I will do it", "standing part architect", "Your brief"):
            self.assertNotIn(injected, joined)
        self.assertEqual(self.texts.count(TYPED), 1)  # the relay with attribution "agent" is not a second copy

    def test_window_and_mtime_bound_what_is_read(self):
        self.assertNotIn("typed before the window", self.texts)
        self.assertNotIn("stale but stamped in the window", self.texts)  # its file's mtime is before --since

    def test_agents_typing_into_a_pane_are_left_out(self):
        self.assertFalse([t for t in self.texts if t.startswith("From the ")])

    def test_steering_kept_and_marked(self):
        steer = next(r for r in self.rows if r["text"] == STEER)
        self.assertTrue(steer["steering"])

    def test_dispatch_typed_turn_carries_its_message_id(self):
        turn = next(r for r in self.rows if r["text"] == "I'm getting super excited.")
        self.assertEqual(turn["source"], "dispatch-turn")
        self.assertEqual(turn["dispatch_id"], "407cb29c-fcc3-406e-8fd8-ecc6d9076ba4")

    def test_shaken_text_is_restored_before_the_history_check(self):
        shaken = next(r for r in self.rows if r["text"] == LONG_TYPED)
        self.assertEqual((shaken["source"], shaken["time"]), ("terminal", "2026-10-28T02:07:00Z"))

    def test_history_only_prompts_and_drafts(self):
        sources = {r["text"]: r["source"] for r in self.rows}
        self.assertEqual(sources[GUIDED], "terminal-history")
        self.assertEqual(sources["/btw what are you working on?"], "terminal-history")
        self.assertNotIn(DRAFT, self.texts)  # a piece of SENT, typed 30 s before it

    def test_a_subagent_someone_typed_into_is_read(self):
        lane = next(r for r in self.rows if r["session"] == LANE)
        self.assertEqual(lane["source"], "terminal")
        self.assertEqual(lane["title"], "🛡️ GuardLane")

    def test_a_kickoff_is_not_the_rename_line_it_holds(self):
        self.assertNotIn(KICKOFF, self.texts)
        self.assertNotIn(KICK_RENAME, self.texts)  # the session typed it into its own pane

    def test_pause_row_stays_labelled_when_no_script_resumed_this_pane(self):
        pause = next(r for r in self.rows if r["text"] == "/pause")
        self.assertEqual((pause["source"], pause["session"], pause["time"]), (PAUSE, LEAD, "2026-10-28T02:50:00Z"))
        self.assertNotIn(RESUME_NOTE, self.texts)  # the Escape call typed into KICKED, not LEAD
        self.assertNotIn(NUDGE, self.texts)  # a call typed into LEAD, but it pressed no Escape

    def test_a_fork_does_not_repeat_its_parents_messages(self):
        self.assertEqual(self.texts.count(TYPED), 1)
        self.assertEqual(self.texts.count("I'm getting super excited."), 1)
        self.assertEqual(next(r["session"] for r in self.rows if r["text"] == TYPED), LEAD)
        self.assertEqual(next(r["session"] for r in self.rows if r["text"] == AFTER_FORK), FORK)

    def test_filters(self):
        self.assertEqual([r["session"] for r in rows(self.root, "--session", "01a17652")], [LANE])
        self.assertEqual([r["text"] for r in rows(self.root, "--grep", "raj ASKED")], [TYPED])
        cut = rows(self.root, "--grep", "Raj", "--max-chars", "10")[0]
        self.assertEqual(cut["text"], "Why are w…")
        self.assertTrue(cut["truncated"])

    def test_text_output(self):
        done = run(self.root, "--grep", "Raj")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"2026-10-28T02:01:00Z  terminal  {LEAD}  🎼 ORCH: orchestration lead  "
                                      f"/home/sami/src/machcsl-riscv\n    {TYPED}\n")
        self.assertIn("left out 3 prompts never typed in a terminal", done.stderr)  # the launch prompt, the brief, the kickoff

    def test_bad_arguments(self):
        self.assertEqual(run(self.root, "--grep", "(").returncode, 2)
        done = subprocess.run([str(SCRIPT), "--since", "yesterday-ish"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)


def tool_call(t: datetime, name: str, arguments: dict) -> dict:
    """An assistant entry with one tool call, as omp writes it."""
    return {"type": "message", "id": "a2", "parentId": "p", "timestamp": stamp(t),
            "message": {"role": "assistant", "content": [{"type": "toolCall", "id": "toolu_1", "name": name, "arguments": arguments}]}}


SENDER = "01a174f7-1434-74b1-acbe-7b0d6fcf412d"
RECEIVER = "01a16c67-0000-7000-8000-000000000001"
WIND = ("Sami, 22:36Z: there are too many agents on this box and the ones that are finished should be spun down. "
        "Your three pull requests are merged, so you are in that group.")
BRIEF = ("You run design r2-b of speed lane R2 (dispatch://INFRA-167, plan Task 4.2). Your design is fewer lia/nia "
         "certificate searches per goal, through a better problem reduction before the oracle. Read these first: "
         "{lanes}/r2/BRIEF.md, skill://choosing-speed-work, and the skills that BRIEF.md names. You make all decisions "
         "inside your own work. Do not ask for approval of them. Your workspace is /home/sami/src/speed-r2-b.")
SEND_BRIEF = """#!/usr/bin/env bash
# Send each lane design session its brief through tmux send-keys.
set -euo pipefail
pane=$1 lane=$2 design=$3
LANES={lanes}
case "$lane-$design" in
  r2-b) idea="fewer lia/nia certificate searches per goal, through a better problem reduction before the oracle" ;;
esac
msg="You run design $lane-$design of speed lane ${{lane^^}} (dispatch://INFRA-167, plan Task 4.2). Your design is $idea. \\
Read these first: $LANES/$lane/BRIEF.md, skill://choosing-speed-work, and the skills that BRIEF.md names. You make all \\
decisions inside your own work. Do not ask for approval of them. Your workspace is /home/sami/src/speed-$lane-$design."
tmux send-keys -t "$pane" -l "$msg"
tmux send-keys -t "$pane" Enter
"""
FILED = "Note from LaneStart: one autoresearch.sh run takes about 8 to 15 minutes, so give each call a timeout of 1800 seconds."
OWN = "Lower METRIC wall_s of autoresearch.sh with METRIC verdict_mismatch at 0, by removing most of the cost."
KEPT = "Okay, fucking let it rip. And keep the pilot on one region until it works."
RENAME = "/rename 🪓 R2B: speed lane r2-b"
PROMPT = ("Call the bash tool exactly once with async set to true (run in background) and timeout 0 for the command: "
          "sleep 90; echo JOB_FINISHED . Do not wait for it, do not poll it, do not call any other tool at all.")
DRIVER = f"""#!/usr/bin/env bash
S=$1
T() {{ timeout 20 tmux -L iwrlane -f /dev/null "$@"; }}
PROMPT="{PROMPT}"
T send-keys -t "$S" -l "$PROMPT"; sleep 1; T send-keys -t "$S" Enter
"""
RESUMED = "From TASKCOACH (task-making lead): you are resumed on Sami's word at 15:10Z. Wrap up only, then stop."
# The 2026-10-28 shape: a script, written by a write call and since deleted, presses Escape on a
# pane that shows the pause screen, then types the wrap-up prompt into it.
RESUME_PY = f'''import subprocess, time
for target in ("%23", "%24"):
    subprocess.run(["tmux", "send-keys", "-t", target, "Escape"])
    time.sleep(3)
    subprocess.run(["tmux", "send-keys", "-t", target, "-l", {RESUMED!r}])
    subprocess.run(["tmux", "send-keys", "-t", target, "Enter"])
'''
FORK2 = "01a17147-0000-7000-8000-000000000002"


class AgentsTypingIntoPanes(unittest.TestCase):
    """Rules 9 and 10 on the shapes found on 2026-10-23/24/25/28: an eval cell that types through a
    helper and never says "tmux", a script that fills in a template, a brief sent from a file, a
    script deleted after it ran (seen only in the write call that made it), an agent sending /rename
    into its own pane, and a script that presses Escape on a paused pane and types a wrap-up prompt."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        root = base / "agent"
        lanes = base / "lanes"
        script = base / "LaneStart" / "send-brief.sh"
        script.parent.mkdir(parents=True)
        script.write_text(SEND_BRIEF.format(lanes=lanes))
        script.chmod(0o755)
        note = base / "LaneStart" / "note.md"
        note.write_text(FILED + "\n")
        brief = BRIEF.format(lanes=lanes)
        # The sender's file was moved off the sessions disk and left as a symlink, as on 2026-10-27.
        moved = write_session(base / "moved" / f"2026-10-25T21-28-32-052Z_{SENDER}.jsonl", SENDER, "/home/sami/src",
                              "🔀 SWITCH", [
            tool_call(at(0), "eval", {"language": "py", "title": "Send wind-down instructions", "code":
                      f'WIND = {{\n "%144": {json.dumps(WIND)},\n}}\nfor p, msg in WIND.items():\n'
                      f'    tm("send-keys","-t",p,"-l",msg); time.sleep(0.5)\n    tm("send-keys","-t",p,"Enter")\n'}),
            tool_call(at(1), "bash", {"command": f"S={script}; $S %80 r2 b && echo sent"}),
            tool_call(at(2), "bash", {"command": f'tmux send-keys -t %81 -l "$(cat {note})"; tmux send-keys -t %81 Enter'}),
            tool_call(at(3), "bash", {"command": f"echo {json.dumps(KEPT)} >> /tmp/quotes.md"}),  # quotes, never types
            tool_call(at(6), "write", {"path": str(base / "gone" / "drive.sh"), "content": DRIVER}),  # deleted since
            tool_call(at(6.2), "bash", {"command": f"bash {base / 'gone' / 'drive.sh'} main"}),
            tool_call(at(7), "write", {"path": str(base / "gone" / "resume.py"), "content": RESUME_PY}),
            tool_call(at(7.1), "bash", {"command": f"python3 {base / 'gone' / 'resume.py'}"}),
        ])
        link = root / "sessions" / "-src" / moved.name
        link.parent.mkdir(parents=True)
        link.symlink_to(moved)
        receiver = [
            user(at(0.5), WIND),
            user(at(1.5), brief),
            user(at(2.5), FILED),
            user(at(3.5), KEPT),
            user(at(4.5), OWN),
            # The receiver forwards what it was sent to another pane: a call of its own, after the text.
            tool_call(at(4.8), "bash", {"command": f"tmux send-keys -t %80 -l {json.dumps(OWN)}"}),
            # It runs a slash command by typing into its own pane: a call of its own, before the text,
            # though the history row's whole second is earlier than the call's millisecond time.
            tool_call(at(5) + timedelta(milliseconds=500), "bash",
                      {"command": f"tmux send-keys -t \"$TMUX_PANE\" -l '{RENAME}' && tmux send-keys -t \"$TMUX_PANE\" Enter"}),
            user(at(6.5), PROMPT),
            user(at(7.3), RESUMED),
        ]
        receiver_dir = root / "sessions" / "-src-speed-r2-b"
        write_session(receiver_dir / f"2026-10-24T05-34-00-000Z_{RECEIVER}.jsonl", RECEIVER, "/home/sami/src/speed-r2-b",
                      "🪓 R2B", receiver)
        # A fork of the receiver holds copies of its entries, the forwarding call included. A fork also
        # gets copies of the files below its parent's directory, so one session id can have two files.
        fork = receiver_dir / f"2026-10-28T02-10-00-000Z_{FORK2}.jsonl"
        write_session(fork, FORK2, "/home/sami/src/speed-r2-b", "🪓 R2B", receiver, timestamp=stamp(at(10)), parentSession=RECEIVER)
        write_session(fork.with_suffix("") / f"2026-10-24T05-34-00-000Z_{RECEIVER}.jsonl", RECEIVER, "/home/sami/src/speed-r2-b",
                      "🪓 R2B", receiver)
        write_history(root, [(WIND, at(0.5), RECEIVER), (brief, at(1.5), RECEIVER), (FILED, at(2.5), RECEIVER),
                             (KEPT, at(3.5), RECEIVER), (OWN, at(4.5), RECEIVER), (RENAME, at(5), RECEIVER),
                             (PROMPT, at(6.5), RECEIVER), ("/pause", at(7.25), RECEIVER), (RESUMED, at(7.3), RECEIVER)])
        cls.done = run(root, "--json")
        cls.texts = [json.loads(r)["text"] for r in cls.done.stdout.splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_eval_cell_typing_through_a_helper(self):
        self.assertNotIn(WIND, self.texts)

    def test_template_script_and_brief_file(self):
        self.assertFalse([t for t in self.texts if t.startswith("You run design")])
        self.assertNotIn(FILED, self.texts)

    def test_deleted_script_seen_in_its_write_call(self):
        self.assertNotIn(PROMPT, self.texts)

    def test_slash_command_typed_into_its_own_pane(self):
        self.assertNotIn(RENAME, self.texts)

    def test_pause_toggle_a_script_pressed_is_left_out(self):
        self.assertNotIn(RESUMED, self.texts)
        self.assertNotIn("/pause", self.texts)  # the call that pressed Escape also typed into this session
        self.assertIn("and 1 pause toggles agents pressed", self.done.stderr)

    def test_typed_text_stays(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr)
        self.assertEqual(self.texts, [KEPT, OWN])  # OWN once: the fork's copies are not read, nor its copied call
        self.assertIn("6 texts agents typed", self.done.stderr)


class LargeFile(unittest.TestCase):
    def test_binary_search_skips_old_entries_and_keeps_new_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            filler = [assistant(at(-600 + i * 0.1), "x" * 2000) for i in range(1500)]  # about 3 MB, all hours early
            write_session(root / "sessions" / "-src" / f"2026-10-21T00-00-00-000Z_{LEAD}.jsonl", LEAD, "/src", "big",
                          filler + [user(at(1), TYPED), assistant(at(200), "after the window"), user(at(201), "late")])
            write_history(root, [(TYPED, at(1), LEAD), ("late", at(201), LEAD)])
            self.assertEqual([r["text"] for r in rows(root)], [TYPED])


class FakeDispatch(http.server.BaseHTTPRequestHandler):
    events: list[dict] = []
    seen: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        self.seen.append(self.headers.get("Authorization", ""))
        if url.path == "/api/v1/issues":
            self.reply([{"key": "LINUX-1157", "updated_at": "2026-10-28T02:59:00Z"}])
        elif url.path == "/api/v1/issues/LINUX-1157/events":
            self.reply([max(self.events, key=lambda e: e["id"])])
        elif url.path == "/api/v1/events":
            since = int(urllib.parse.parse_qs(url.query).get("since", ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for e in self.events:
                if e["id"] > since:
                    self.wfile.write(f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())
            self.wfile.write(b": heartbeat\n\n")
        else:
            self.send_error(404)

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def event(eid: int, t: datetime, kind: str, payload: dict, actor: str = "sami@theorem.dev", issue: str | None = "LINUX-1157") -> dict:
    return {"id": eid, "issue_key": issue, "artifact_id": None, "project": "LINUX", "seq": eid, "type": kind,
            "actor": {"kind": "user", "id": actor}, "notify": True, "created_at": stamp(t), "payload": payload}


class DispatchOption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = base / "agent"
        Store(cls.root)
        older = [event(i, at(-300 + i), "artifact.version", {"name": "spec.md"}) for i in range(1, 200)]
        FakeDispatch.events = older + [
            event(300, at(-65), "comment.created", {"id": "c-early", "body": "before the window"}),
            event(301, at(5), "message.created", {"id": "407cb29c-fcc3-406e-8fd8-ecc6d9076ba4", "body": "I'm getting super excited.",
                                                  "author": {"id": "sami@theorem.dev", "kind": "user"}, "target": f"session:{LEAD}"},
                  issue=None),
            event(302, at(6), "message.created", {"id": "99999999-0000-4000-8000-000000000000", "body": "Someone else's Dispatch message.",
                                                  "author": {"id": "tewaro@theorem.dev", "kind": "user"}, "target": f"session:{LEAD}"},
                  actor="tewaro@theorem.dev", issue=None),
            event(303, at(38), "comment.created", {"id": "8dbb79ac-7e38-47e5-8913-e5f673adaa8b", "body": "Why are we relitigating it?",
                                                   "ask_question": "Does Lean start on each edge when its two ends agree?"}),
            event(304, at(39), "ask.answered", {"id": "d3a51496-e7ed-4044-864a-0205e8d61e88", "question": "Where does state live?",
                                                "answer": {"selected": ["English in git, state in Postgres"], "text": None},
                                                "author": {"kind": "session", "id": LEAD, "origin": {"session_title": "ORCH"}}}),
            event(305, at(40), "user_state.updated", {"login": "sami@theorem.dev"}),
            event(306, at(41), "comment.created", {"id": "c-other", "body": "not Sami's"}, actor="tewaro@theorem.dev"),
            event(307, at(42), "message.created", {"id": "m-lane", "body": "/rename gateway", "target": f"session:{LANE}",
                                                   "author": {"id": "sami@theorem.dev", "kind": "user"}}, issue=None),
            event(308, at(42.1), "message.delivery", {"message_id": "m-lane", "title": "⛩️ GATE: gateway"}, actor="system", issue=None),
            event(309, at(50), "artifact.approved", {"name": "spec.md", "reason": "Ship it", "version": 9}),
            event(310, at(130), "comment.created", {"id": "c-late", "body": "after the window"}),
        ]
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDispatch)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        config = base / "envoy.json"
        config.write_text(json.dumps({"dispatch": {"enabled": True, "serverUrl": f"http://127.0.0.1:{cls.server.server_port}",
                                                   "token": "test-token"}}))
        env = {"ENVOY_CONFIG_FILE": str(config), "DISPATCH_URL": "", "DISPATCH_TOKEN": "", "DISPATCH_TOKEN_FILE": ""}
        cls.rows = rows(cls.root, "--dispatch", env=env)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def test_dispatch_rows_are_labelled_and_windowed(self):
        found = [(r["source"], r["text"]) for r in self.rows if r["source"].startswith("DISPATCH")]
        self.assertEqual(found, [
            ("DISPATCH comment", "Why are we relitigating it?"),
            ("DISPATCH answer", "[English in git, state in Postgres]"),
            ("DISPATCH message", "/rename gateway"),
            ("DISPATCH approval", "Ship it"),
        ])

    def test_dispatch_rows_carry_refs_and_context(self):
        comment = next(r for r in self.rows if r["source"] == "DISPATCH comment")
        self.assertEqual(comment["ref"], "dispatch://LINUX-1157/comment/8dbb79ac-7e38-47e5-8913-e5f673adaa8b")
        self.assertEqual(comment["context"], "reply to ask: Does Lean start on each edge when its two ends agree?")
        answer = next(r for r in self.rows if r["source"] == "DISPATCH answer")
        self.assertEqual(answer["ref"], "dispatch://LINUX-1157/ask/d3a51496-e7ed-4044-864a-0205e8d61e88")
        self.assertEqual(answer["session"], LEAD)
        self.assertEqual(answer["title"], "🎼 ORCH: orchestration lead")  # the local session's title wins
        message = next(r for r in self.rows if r["source"] == "DISPATCH message")
        self.assertEqual((message["session"], message["title"]), (LANE, "🛡️ GuardLane"))

    def test_dispatch_turns_are_checked_and_not_repeated(self):
        turns = [r for r in self.rows if r["source"] == "dispatch-turn"]
        self.assertEqual([(r["text"], r["author"]) for r in turns], [("I'm getting super excited.", "sami@theorem.dev")])
        self.assertEqual(sum(r["text"] == "I'm getting super excited." for r in self.rows), 1)

    def test_bearer_comes_from_envoy_json(self):
        self.assertTrue(FakeDispatch.seen)
        self.assertEqual(set(FakeDispatch.seen), {"Bearer test-token"})


if __name__ == "__main__":
    unittest.main()
