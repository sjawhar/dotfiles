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
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = load("check-findings")
enumerate_messages = load("enumerate-messages")

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


if __name__ == "__main__":
    unittest.main()
