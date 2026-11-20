#!/usr/bin/env python3
"""Regression tests for ste-lint: no rule reads a link's address.

The case these were written for: on 2026-11-17 ste-lint gave a hard synonym-rotation
finding for `launch` against a `start` in the prose, where `launch` was a word of a
link's fragment (`[Statement](dispatch://INFRA-3839/artifact/level-two-final-level-two-md
#daemon-refused-registration-costs-no-launch-attempt).`), and seven more like it in two
statement files. An address is a name, not prose, as a code span is: a rule that counts or
compares words reads neither, and a finding after one keeps its column in the source.

Run: python3 scripts/tests/test_ste_lint.py (STE_LINT_SCRIPT names another copy to test).
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(os.environ.get("STE_LINT_SCRIPT") or Path(__file__).resolve().parents[1] / "ste-lint")
LOADER = importlib.machinery.SourceFileLoader("ste_lint", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
STE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(STE)

FRAGMENT = "dispatch://INFRA-3839/artifact/level-two-final-level-two-md#"
# The eight lines ste-lint flagged in statement-notes-01.md and statement-notes-02.md
# (lanes/level-two-v2/work/lead3/language-debug-3.json), each after a sentence that uses the
# word of its group that came first in its file.
REPRO = (
    ("Start the job.", "daemon-refused-registration-costs-no-launch-attempt"),
    ("Show the result.", "display-views-use-the-servers-reading-of-stored-text"),
    ("Check the log.", "refused-field-saves-restore-confirmed-values-except-title-drafts"),
    ("Remove the file.", "authors-can-edit-and-delete-posts-without-a-browser"),
    ("Start the job.", "failure-measures-begin-at-the-first-enabled-failure-source"),
    ("Fix the fault.", "envoy-repairs-its-copy-of-the-role-claims"),
    ("Change the value.", "texts-a-change-writes-or-alters-are-true-at-its-head"),
    ("Repair the record.", "dispatch-unset-public-address-is-fixed-or-disabled"),
)


def terms(first, last):
    return " ".join(f"term{number}" for number in range(first, last + 1))


class SteLintTest(unittest.TestCase):
    def findings(self, text):
        """Each finding as (line, column, rule, match), after checking that the source
        holds the match at that line and column, so every expected column is exact."""
        findings, _ = STE.lint(text)
        lines = text.splitlines()
        for finding in findings:
            if finding["rule"] in ("long-sentence", "dangling-conjunction"):
                continue
            source = lines[finding["line"] - 1][finding["col"] - 1:]
            self.assertTrue(source.startswith(finding["match"]),
                            f"{finding} is not at its column in {lines[finding['line'] - 1]!r}")
        return [(f["line"], f["col"], f["rule"], f["match"]) for f in findings]

    def test_the_eight_reported_lines_give_no_finding(self):
        for keeper, fragment in REPRO:
            with self.subTest(fragment=fragment):
                text = f"{keeper}\n\n[Statement]({FRAGMENT}{fragment}).\n"
                self.assertEqual(self.findings(text), [])

    def test_a_bare_dispatch_link_with_a_fragment(self):
        text = ("Start the job.\n"
                "Read dispatch://INFRA-3839/artifact/one#launch-attempt first; then stop.\n")
        self.assertEqual(self.findings(text), [(2, 61, "semicolon", ";")])

    def test_an_https_url_with_a_path_of_words(self):
        text = "See https://example.test/guides/seamless-state-of-the-art-setup;v=2 now; then stop.\n"
        self.assertEqual(self.findings(text), [(1, 72, "semicolon", ";")])

    def test_a_markdown_link_whose_address_holds_the_word(self):
        text = ("Start the job.\n"
                "Read [the guide](https://example.test/launch-guide) first; then stop.\n")
        self.assertEqual(self.findings(text), [(2, 58, "semicolon", ";")])
        # A destination with no scheme: a path in this repository.
        text = ("Start the job.\n"
                "Read [the notes](./launch-notes.md) first; then stop.\n")
        self.assertEqual(self.findings(text), [(2, 42, "semicolon", ";")])

    def test_a_markdown_link_whose_bracket_words_hold_the_word_is_still_read(self):
        text = ("Start the job.\n"
                "Read [how to launch it](https://example.test/guide) first.\n")
        self.assertEqual(self.findings(text), [(2, 14, "synonym-rotation", "launch")])

    def test_an_image_keeps_its_words_and_an_autolink_is_an_address(self):
        text = ("Start the job.\n"
                "![the launch diagram](https://example.test/initiate.png) and "
                "<https://example.test/begin-here>; stop.\n")
        self.assertEqual(self.findings(text), [
            (2, 7, "synonym-rotation", "launch"),
            (2, 95, "semicolon", ";"),
        ])

    def test_a_link_in_a_table_cell(self):
        text = ("| Step | Source |\n"
                "| --- | --- |\n"
                "| Start | [guide](dispatch://INFRA-1/artifact/a#launch-it); "
                "then dispatch://INFRA-1/artifact/b#initiate-it |\n")
        self.assertEqual(self.findings(text), [(3, 59, "semicolon", ";")])

    def test_a_link_at_the_start_and_at_the_end_of_a_line(self):
        text = ("Start the job.\n"
                "https://example.test/launch-page holds the steps; "
                "read dispatch://INFRA-1/artifact/c#initiate-steps\n"
                "[The steps](https://example.test/begin-here) are short; read them.\n")
        self.assertEqual(self.findings(text), [
            (2, 49, "semicolon", ";"),
            (3, 55, "semicolon", ";"),
        ])

    def test_two_links_on_one_line(self):
        text = ("Stop the job.\n"
                "Read [one](https://example.test/halt-now) and "
                "dispatch://INFRA-1/artifact/b#terminate-now; then stop.\n")
        self.assertEqual(self.findings(text), [(2, 90, "semicolon", ";")])

    def test_a_link_in_a_code_span_is_not_read_as_before(self):
        text = ("Start the job.\n"
                "Run `ste-lint dispatch://INFRA-1/artifact/c#launch-it` now.\n")
        self.assertEqual(self.findings(text), [])

    def test_a_finding_after_a_code_span_keeps_its_column(self):
        self.assertEqual(self.findings("Use `abc` here; then go.\n"), [(1, 15, "semicolon", ";")])
        text = ("Start the job.\n"
                "Run `see https://example.test/launch` now; then stop.\n")
        self.assertEqual(self.findings(text), [(2, 42, "semicolon", ";")])
        text = "Start the job.\nUse `x` then launch it.\n"
        self.assertEqual(self.findings(text), [(2, 14, "synonym-rotation", "launch")])

    def test_a_url_followed_by_a_full_stop(self):
        text = "Start the job.\nRead https://example.test/launch-notes.\n"
        self.assertEqual(self.findings(text), [])
        # The full stop still ends the sentence: 21 and 15 words, not one of 36.
        text = f"{terms(1, 19)} at https://example.test/notes. {terms(20, 34)}.\n"
        self.assertEqual(self.findings(text), [])

    def test_a_url_followed_by_a_closing_bracket(self):
        text = "Start the job (see https://example.test/launch-notes); then stop.\n"
        self.assertEqual(self.findings(text), [(1, 54, "semicolon", ";")])
        text = "Start the job (see [the notes](https://example.test/launch-notes)); then stop.\n"
        self.assertEqual(self.findings(text), [(1, 67, "semicolon", ";")])
        # A bracket pair inside the address is the address's own.
        text = "Start the job.\nRead https://example.test/wiki/Rocket_(launch) now; then stop.\n"
        self.assertEqual(self.findings(text), [(2, 51, "semicolon", ";")])

    def test_where_a_bare_address_ends(self):
        for text, prose in (
                ("(see https://example.test/a).", "(see )."),
                ("Read https://example.test/a, then go.", "Read , then go."),
                ('Say "https://example.test/it\'s" now.', 'Say "" now.'),
                ("It is “https://example.test/a-md.” Then go.", "It is “.” Then go."),
                ("See https://example.test/a—then go.", "See —then go."),
                ("**https://example.test/a**", "****"),
                ("[a](https://example.test/b \"The title\")", "[a]( \"The title\")"),
                ("[a](<./my notes.md>) and [b][label-x].", "[a]() and [b]."),
                ("[label-x]: ./launch.md \"The title\"", " \"The title\""),
                ("[^1]: Launch it.", "[^1]: Launch it."),
                ("[Note]: the server starts.", "[Note]: the server starts.")):
            with self.subTest(text=text):
                kept, columns = STE._prose(text)
                self.assertEqual(kept, prose)
                self.assertEqual([text[column] for column in columns], list(kept))

    def test_an_address_is_no_word_of_a_sentence(self):
        # The linter counts no code span as a word, and an address is a name in the same way:
        # 25 words and an address are 25 words.
        text = f"{terms(1, 12)} https://example.test/notes {terms(13, 25)}.\n"
        self.assertEqual(self.findings(text), [])
        self.assertEqual(STE.lint(text)[1], 25)
        # The words in a link's brackets are prose and count.
        text = f"{terms(1, 24)} [two words](https://example.test/notes).\n"
        self.assertEqual(self.findings(text), [(1, 1, "long-sentence", "26 words")])

    def test_a_list_item_that_ends_with_an_address(self):
        self.assertEqual(self.findings("- Read the notes at https://example.test/notes-and\n"), [])
        self.assertEqual(self.findings("- Read the notes\n  at https://example.test/stand-or\n"), [])
        # The address is an operand: the conjunction before it does not dangle.
        self.assertEqual(self.findings("- Compare this and https://example.test/b\n"), [])
        # A conjunction after a link dangles, at its own column; the match shows the address
        # as an operand, as it shows a code span.
        self.assertEqual(
            self.findings("- Read the notes at\n  [the page](https://example.test/a) and\n"),
            [(2, 38, "dangling-conjunction", "[the page]( CODE ) and")])

    def test_a_directive_value_with_a_link_to_an_anchor(self):
        text = ("Start the job.\n"
                ':::check{#v-one run="Read [the notes](#launch-notes) and stop the job."}\n'
                ":::\n")
        self.assertEqual(self.findings(text), [])

    def test_reference_links_and_footnotes(self):
        text = ("Start the job.\n"
                "Read [the notes][launch-notes] first.\n"
                "\n"
                '[launch-notes]: ./initiate-notes.md "The notes"\n')
        self.assertEqual(self.findings(text), [])
        # A footnote's text is prose.
        text = "Start the job.\nRead the notes.[^1]\n\n[^1]: Launch it after the check.\n"
        self.assertEqual(self.findings(text), [(4, 7, "synonym-rotation", "Launch")])

    def test_the_command_passes_the_minimal_repro(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identifier-only.md"
            path.write_text("Start the session.\n\n"
                            "[Evidence](dispatch://INFRA-3839/artifact/one#launch).\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("0 violations (0 hard, baseline 0), 4 words", run.stdout)

    def test_the_selftest_passes(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--selftest"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, "selftest OK\n")


if __name__ == "__main__":
    unittest.main()
