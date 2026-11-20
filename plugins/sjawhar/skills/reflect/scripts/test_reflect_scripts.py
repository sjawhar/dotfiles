#!/usr/bin/env python3
"""Tests for the reflect scripts' quote checking and message enumeration.

Stdlib unittest only — run with `python3 -m unittest -v test_reflect_scripts` from this
directory. Nothing here calls tq or the network: every test drives a pure function, or the
check-findings CLI over a records directory built under /tmp.

What each test holds to, with the defect it was written for (reflect run of 2026-10-31):
  check-findings, a quote of his that spans a newline inside a tool payload or an envelope
    -> test_quote_across_an_escaped_newline, test_escaped_double_quotes
  check-findings, his words in a Dispatch envelope, and in a Dispatch-only record
    -> test_sami_words_include_the_dispatch_envelope, test_sami_words_of_a_dispatch_only_record
  check-findings, a quote of the agent's words is still not his
    -> test_agent_words_are_not_sami, test_cli_flags_the_agent_quote_and_passes_his
  enumerate-messages, the words the text index can be asked for
    -> test_index_tokens_are_whole_words, test_index_tokens_hold_in_any_containing_text
  enumerate-messages, a window written with a Z, and the rows that are nobody's message
    -> test_window_takes_a_z_suffix, test_legion_and_other_people_hosts_are_excluded

And for the defects the run of 2026-11-08 hit (report.md section 8):
  check-findings, a quote carrying a hard hyphen, markdown bold, a shell-escaped quote or a
    second round of JSON escaping -> test_quote_with_a_hard_hyphen_bold_and_a_shell_escape
  count-types export, a run whose Dispatch-only records are indexed in a sibling file
    -> test_export_reads_every_records_index
  enumerate-messages, a proof scheduler's item files and resume notices read as his messages
    -> test_scheduler_items_are_excluded_and_his_own_attachment_is_not
  SKILL.md, a command written without the flags its script requires
    -> test_skill_runs_extract_dispatch_human_with_its_required_flags

And for experiments-readout, which still read the rows of a message extractor deleted in October:
  its turns, read from enumerate-messages.py rows (the excluded ones, restart copies, Dispatch
    deliveries, and the store's naive UTC against the draw's ISO time)
    -> test_turns_are_the_enumerated_rows_of_his_own_words
  its draws, where a restart copies the old session's draw ahead of its own, and a directory is
    named like a session file -> test_a_restart_copy_is_measured_on_its_own_draw
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = load("check-findings")
enumerate_messages = load("enumerate-messages")
readout = load("experiments-readout")

RECORD = """# Record M0001

## Sami's previous message in this session (L10)

[L10 10-30 22:19:52] SAMI:
Stop opening asks I already answered.

## Before: the agent's work since Sami's previous message (line 10)

[L20] TOOL SendMessage:
{"to":"peer","message":"Coordinator, correction to my last message.\\nThe framework files go\\nthrough the loop too, and he said \\"no caps\\" about the builders."}
[L30 10-30 22:24:40] AGENT SAID:
Sorry, that was my shorthand. Here it is in full.

## SAMI'S MESSAGE (L40, verbatim)


### The Dispatch envelope it arrived in
envoy:
  to: you (01a1)
  from: dispatch
  id: dispatch-125836
  dispatch:
    owner: BET-466
    type: comment.created
    actor:
      kind: user
      id: sami@theorem.dev
    body: "Don't cap the builders.\\nMeasure the load first, and say what you measured."

## After: the agent's work up to its next yield or Sami's next message
[L50 10-30 22:25:25] AGENT SAID:
Understood, no cap.
"""

DISPATCH_ONLY = """# M9001: Sami's Dispatch comment that reached no agent session

- id: M9001
- source: Dispatch API only (no transcript carried this comment to any session)

## His message (verbatim)

Do we want to add a condition or just fix it?

## The issue thread (dispatch read --issue OSSV-257, read 2026-10-31T19:35:52Z)

```
Comment by the agent: I propose we add a condition to the budget mark.
```
"""


class CheckFindings(unittest.TestCase):
    def test_quote_across_an_escaped_newline(self) -> None:
        """His message is stored escaped inside the envelope, so `\\n` stands for the newline."""
        text = check.variants(RECORD)
        self.assertTrue(check.found("Don't cap the builders. Measure the load first", text))
        self.assertTrue(check.found("Don't cap the builders.\nMeasure the load first", text))

    def test_escaped_double_quotes(self) -> None:
        self.assertTrue(check.found('he said "no caps" about the builders', check.variants(RECORD)))

    def test_absent_quote_is_still_absent(self) -> None:
        self.assertFalse(check.found("Cap the builders at four", check.variants(RECORD)))

    def test_ellipsis_still_splits_a_quote(self) -> None:
        self.assertTrue(check.found("Don't cap the builders ... say what you measured", check.variants(RECORD)))
        self.assertFalse(check.found("say what you measured ... Don't cap the builders", check.variants(RECORD)))

    def test_sami_words_include_the_dispatch_envelope(self) -> None:
        sami = check.variants(check.sami_words(RECORD))
        self.assertTrue(check.found("Don't cap the builders. Measure the load first", sami))
        self.assertTrue(check.found("Stop opening asks I already answered", sami))

    def test_agent_words_are_not_sami(self) -> None:
        sami = check.variants(check.sami_words(RECORD))
        self.assertTrue(check.found("Sorry, that was my shorthand", check.variants(RECORD)))
        self.assertFalse(check.found("Sorry, that was my shorthand", sami))
        self.assertFalse(check.found("Coordinator, correction to my last message", sami))

    def test_sami_words_of_a_dispatch_only_record(self) -> None:
        sami = check.variants(check.sami_words(DISPATCH_ONLY))
        self.assertTrue(check.found("Do we want to add a condition or just fix it?", sami))
        self.assertFalse(check.found("I propose we add a condition to the budget mark", sami))

    def test_cli_flags_the_agent_quote_and_passes_his(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "M0001.md").write_text(RECORD)
            (d / "batches.jsonl").write_text(
                json.dumps({"batch": "B001", "records": ["M0001"], "paths": [str(d / "M0001.md")]}) + "\n"
            )
            (d / "rows.jsonl").write_text(
                json.dumps({
                    "record": "M0001",
                    "findings": [
                        {"instruction": "Don't cap the builders.\nMeasure the load first",
                         "action": "Understood, no cap.",
                         "pushback": "Stop opening asks I already answered"},
                        {"instruction": "Don't cap the builders", "action": "Understood, no cap.",
                         "pushback": "Sorry, that was my shorthand. Here it is in full."},
                    ],
                    "sami_patterns": [{"quote": "Measure the load first, and say what you measured"}],
                }) + "\n"
            )
            out = d / "check.json"
            subprocess.run(
                [sys.executable, str(HERE / "check-findings.py"), str(d), str(d / "rows.jsonl"), "--out", str(out)],
                capture_output=True, check=False,
            )
            report = json.loads(out.read_text())
            self.assertEqual(report["bad_quotes"], [])
            self.assertEqual([f["finding"] for f in report["pushback_not_sami"]], [1])
            self.assertEqual((report["findings"], report["patterns"], report["unpushed"]), (2, 1, 0))

    def test_quote_with_a_hard_hyphen_bold_and_a_shell_escape(self) -> None:
        """86% of the 2026-11-08 run's 404 flags were these shapes, not misquotes."""
        record = "[L10 11-08 09:00:00] SAMI:\nDon't re-run it: the merge-queue run is the verdict.\n"
        text = check.variants(record)
        self.assertTrue(check.found("the merge\u2011queue run is the verdict", text))
        self.assertTrue(check.found("the **merge-queue run** is the verdict", text))
        self.assertTrue(check.found("Don'\\''t re-run it", text))
        self.assertTrue(check.found("Don't re-run it:\\\\nthe merge-queue run", text))
        self.assertFalse(check.found("the merge-queue run is advisory", text))


class EnumerateMessages(unittest.TestCase):
    def test_index_tokens_are_whole_words(self) -> None:
        self.assertEqual(
            enumerate_messages.index_tokens("Stop opening asks I already answered now"),
            ["already", "answered", "asks", "i", "opening"],
        )

    def test_index_tokens_drop_a_cut_word(self) -> None:
        """A needle is the first 48 characters of a line, so its last word may be half a word."""
        self.assertNotIn("answe", enumerate_messages.index_tokens("Stop opening asks I already answe"))

    def test_index_tokens_hold_in_any_containing_text(self) -> None:
        """Every token must be a whole token of any text holding the needle, under either split rule."""
        import re

        needle = "why is the lander taking so long? gcp-2 at v3.4"
        text = f"omp -p '{needle}1 more' --session x"
        for token in enumerate_messages.index_tokens(needle):
            self.assertRegex(text.lower(), rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])")

    def test_needle_is_the_first_filled_line(self) -> None:
        self.assertEqual(enumerate_messages.needle("\n\n  why is the lander taking so long?\nmore\n"),
                         "why is the lander taking so long?")

    def test_window_takes_a_z_suffix(self) -> None:
        """The skill's own example writes the window with a Z; the store takes only naive UTC."""
        self.assertEqual(enumerate_messages.window("2026-11-06T15:00:00Z"), "2026-11-06 15:00:00")

    def test_window_takes_an_offset_and_the_plain_forms(self) -> None:
        self.assertEqual(enumerate_messages.window("2026-11-06T10:00:00-05:00"), "2026-11-06 15:00:00")
        self.assertEqual(enumerate_messages.window(" 2026-11-06 15:00:00 "), "2026-11-06 15:00:00")
        self.assertEqual(enumerate_messages.window("2026-11-06 15:00"), "2026-11-06 15:00:00")

    def test_legion_and_other_people_hosts_are_excluded(self) -> None:
        """A Legion pod's typed rows are the daemon's prompts; machcsl-gcp-* is another person's."""
        rows = [
            {"session_id": "s-legion", "host": "legion-infra", "ts": "2026-11-06 15:01:00", "raw_file": "f",
             "raw_line": 851, "text": "Continue INFRA-1240. Phase: reviewing.", "cwd": None, "title": None},
            {"session_id": "s-mach", "host": "machcsl-gcp-1", "ts": "2026-11-06 15:02:00", "raw_file": "f",
             "raw_line": 7, "text": "Tool loaded.", "cwd": None, "title": None},
            {"session_id": "s-sami", "host": "sami-devbox", "ts": "2026-11-06 15:03:00", "raw_file": "f",
             "raw_line": 9, "text": "why is the lander taking so long?", "cwd": None, "title": None},
        ]
        life = {r["session_id"]: (2, 1) for r in rows}  # none is its session's only typed row
        with unittest.mock.patch.object(enumerate_messages, "typed_rows", lambda *a: rows), \
                unittest.mock.patch.object(enumerate_messages, "first_typed_line", lambda *a: life), \
                unittest.mock.patch.object(enumerate_messages, "agent_sent", lambda *a: set()), \
                unittest.mock.patch.object(enumerate_messages, "dispatch_rows", lambda *a: []):
            out, _ = enumerate_messages.build("2026-11-06 15:00:00", "2026-11-07 15:00:00", "^sami@")
        self.assertEqual([r["excluded"] for r in out], ["legion-daemon-prompt", "not-his-machine", None])

    def test_scheduler_items_are_excluded_and_his_own_attachment_is_not(self) -> None:
        """A proof scheduler's item file, standing brief and resume notice are its text, not his."""
        texts = [
            '<file name="/agents/sched/sessions/owner/iface%3ARtWorld/item-3825.md">\n'
            "# Item 3825 for owner iface:RtWorld\n\nWrite the contract.\n",
            '<file name="/agents/sched/records/iface%3ARtWorld/brief.md">\n'
            "# Standing brief of owner iface:RtWorld\n\nYou own the interface contract.\n",
            "Your item is /agents/sched/sessions/owner/iface%3ARtWorld/item-178.md: read it again "
            "if your context no longer holds its header. Then: continue the step.",
            '<file name="/mnt/nvme/sami/tmp/brief.md">\n# Goal\nRead-only review of the final head.\n',
        ]
        rows = [
            {"session_id": f"s{i}", "host": "sami-devbox", "ts": f"2026-11-06 15:0{i}:00", "raw_file": "f",
             "raw_line": 5 + i, "text": t, "cwd": None, "title": None}
            for i, t in enumerate(texts)
        ]
        life = {r["session_id"]: (2, 1) for r in rows}
        with unittest.mock.patch.object(enumerate_messages, "typed_rows", lambda *a: rows), \
                unittest.mock.patch.object(enumerate_messages, "first_typed_line", lambda *a: life), \
                unittest.mock.patch.object(enumerate_messages, "agent_sent", lambda *a: set()), \
                unittest.mock.patch.object(enumerate_messages, "dispatch_rows", lambda *a: []):
            out, _ = enumerate_messages.build("2026-11-06 15:00:00", "2026-11-07 15:00:00", "^sami@")
        self.assertEqual([r["excluded"] for r in out],
                         ["scheduler-item", "scheduler-item", "scheduler-item", None])


class CountTypes(unittest.TestCase):
    def test_export_reads_every_records_index(self) -> None:
        """A run indexes its Dispatch-only records beside records.jsonl; export must find them."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "records.jsonl").write_text(json.dumps({"id": "M0001", "session_id": "s1"}) + "\n")
            (d / "records-dispatch.jsonl").write_text(json.dumps({"id": "M9001", "session_id": "s2"}) + "\n")
            rows = d / "rows.jsonl"
            rows.write_text("".join(
                json.dumps({"record": rid, "findings": [{"instruction": "i", "action": "a"}]}) + "\n"
                for rid in ("M0001", "M9001")
            ))
            out = d / "findings.jsonl"
            p = subprocess.run(
                [sys.executable, str(HERE / "count-types.py"), "export", str(d), str(rows), "--out", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            got = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual([(x["id"], x["session_id"]) for x in got], [("M0001#0", "s1"), ("M9001#0", "s2")])


def experiments_entry(root: str, ts: str, on: bool) -> str:
    """One `experiments` entry as omp writes it: compact JSON, so the readout's marker finds it."""
    return json.dumps({
        "type": "custom", "customType": "experiments", "timestamp": ts,
        "data": {"v": 1, "rootSessionId": root,
                 "features": {"selfcompact": {"gate": "random", "draw": "on" if on else "off", "on": on}}},
    }, separators=(",", ":"))


class ExperimentsReadout(unittest.TestCase):
    OLD = "01a19679-aacf-7456-a9fa-87327b9ecd86"
    NEW = "01a19ed6-98e2-730d-a75f-1cc78c7362f4"
    OTHER = "01a19ed6-98e6-7555-a350-d306431f27e2"

    @staticmethod
    def row(**fields) -> dict:
        """A row as `enumerate-messages.py --out` writes it."""
        return {"source": "tq", "kind": "typed", "host": "host-a", "raw_file": "f", "raw_line": 1,
                "cwd": None, "title": None, "excluded": None, "also_in": [], "copy_of": None, **fields}

    def test_turns_are_the_enumerated_rows_of_his_own_words(self) -> None:
        """An excluded row and a restart copy are no turn of his; a Dispatch event counts in each
        session it reached; the store's naive UTC is compared as a time, not as a string."""
        since = "2026-11-06T15:00:00.000Z"
        sessions = {sid: {"since": since} for sid in (self.OLD, self.NEW, self.OTHER)}
        rows = [
            self.row(id="M0001", ts="2026-11-06 14:59:59.999", session_id=self.OLD, text="before the draw"),
            self.row(id="M0002", ts="2026-11-06 15:01:00.698", session_id=self.OLD, text="Why is CI still red?",
                     also_in=[self.NEW]),
            self.row(id="M0003", ts="2026-11-06 15:01:00.698", session_id=self.NEW, text="Why is CI still red?",
                     excluded="restart-copy", copy_of=self.OLD),
            self.row(id="M0004", ts="2026-11-06 15:02:00.000", session_id=self.OLD, text="Review PR 12, report",
                     excluded="agent-sent"),
            self.row(id="M0005", kind="dispatch", ts="2026-11-06 15:03:00.000", session_id=self.OLD,
                     text="Stop opening asks I already answered", dispatch_id="e1", excluded=None,
                     also_in=[self.OTHER, "01a1aaaa-0000-7000-8000-000000000000"]),
            self.row(id="M0006", ts="2026-11-06 15:04:00.000", session_id="01a1bbbb-0000-7000-8000-000000000000",
                     text="a session with no draw"),
            # A second message of the same session at the same time is a turn of its own.
            self.row(id="M0007", ts="2026-11-06 15:01:00.698", session_id=self.OLD, text="and the docs too"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            messages = Path(tmp) / "messages.jsonl"
            messages.write_text("".join(json.dumps(r) + "\n" for r in rows))
            turns = readout.load_turns([messages], sessions)
        self.assertEqual(sorted((t["sid"], t["text"]) for t in turns), [
            (self.OLD, "Stop opening asks I already answered"),
            (self.OLD, "Why is CI still red?"),
            (self.OLD, "and the docs too"),
            (self.OTHER, "Stop opening asks I already answered"),
        ])

    def test_a_restart_copy_is_measured_on_its_own_draw(self) -> None:
        """A restart writes the old session's entries, its draw included, ahead of the new one's."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "-src-project"
            proj.mkdir()
            (proj / f"2026-11-03T00-37-06-402Z_{self.NEW}.jsonl").write_text(
                experiments_entry(self.OLD, "2026-11-01T09:39:02.050Z", True) + "\n"
                + experiments_entry(self.NEW, "2026-11-03T00:38:20.197Z", False) + "\n")
            (proj / f"2026-11-16T00-58-22-771Z_{self.OTHER}.jsonl").mkdir()
            sessions = readout.load_sessions([tmp], readout.EXCLUDE_DEFAULT)
        self.assertEqual({sid: (s["since"], s["arms"]) for sid, s in sessions.items()},
                         {self.NEW: ("2026-11-03T00:38:20.197Z", {"selfcompact": False})})


class SkillDoc(unittest.TestCase):
    def test_skill_runs_extract_dispatch_human_with_its_required_flags(self) -> None:
        """The skill's command must carry every flag its script requires, or step 1 exits on usage."""
        import re

        line = next(ln for ln in (HERE.parent / "SKILL.md").read_text().splitlines()
                    if "extract-dispatch-human.py" in ln)
        required = re.findall(r'add_argument\("(--[a-z-]+)", required=True',
                              (HERE / "extract-dispatch-human.py").read_text())
        self.assertTrue(required)
        for flag in required:
            self.assertIn(flag, line)


if __name__ == "__main__":
    unittest.main()
