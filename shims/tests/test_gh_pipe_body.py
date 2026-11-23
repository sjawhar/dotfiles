#!/usr/bin/env python3
"""shims/gh gives gh every byte of a text file the call names by a descriptor that is a pipe, after the checks read it.

`gh pr edit 11 --body-file <(…)` names the read end of a pipe, /dev/fd/63, whose bytes can be read once. A check that
read them would take them from gh, so the public text gate refused such a call, the leak-term check refused it for any
target not confirmed private, and on 2026-11-23 a builder's body edit never reached GitHub. The shim now reads each such
stream into a private unlinked file before any check and points the same descriptor at that file (gh_shim_buffer_fds),
so each check and then gh read the same bytes.

A fake gh, first on PATH, records each call's arguments and the bytes it read from each file the call names, and
answers the visibility reads of scripts/github-repo-visibility. The term list is the encrypted test list of
leak_terms_fixture.py, so the canary is a listed term and no private term appears anywhere. Each case runs a caller's
bash line through LeakTestCase.launch, so the process substitution is bash's own."""
from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from leak_terms_fixture import fixture_environment
from test_gh_leak_terms import CANARY, CLEAN, PRIVATE, PUBLIC, TEXT
from test_leak_terms import SESSION, LeakTestCase, base_environment, write_executable

SHIMS = Path(__file__).resolve().parents[1]
DOTFILES = SHIMS.parent
GH = SHIMS / "gh"
FAKE_GH = r"""#!/bin/bash
# A visibility read (scripts/github-repo-visibility): answered from $VIS_DIR/<owner>_<repo>, or refused.
if [[ "${1:-}" == api && "${2:-}" == --hostname && "${3:-}" == github.com && "${4:-}" == repos/* && "${5:-}" == --jq \
    && "${6:-}" == .visibility ]]; then
    answer="$VIS_DIR/${4#repos/}"
    answer="${answer%/*}_${answer##*/}"
    [[ -f "$answer" ]] || exit 1
    cat -- "$answer"
    exit 0
fi
n=$(find "$CALLS" -name '*.args' | wc -l)
printf '%s\0' "$@" > "$CALLS/$n.args"
# The bytes gh reads from each file the call names, in order: an option's file, a field's @file, `-` for stdin. -O
# names a file gh writes.
prev="" i=0
for a in "$@"; do
    path=""
    case "$prev" in
        --body-file | -F | --input) path="$a" ;;
        -O) printf 'asset\n' > "$a" ;;
    esac
    case "$a" in --body-file=* | --input=*) path="${a#*=}" ;; esac
    [[ "$path" != *=@* ]] || path="${path#*=@}"
    if [[ -n "$path" ]]; then
        if [[ "$path" == - ]]; then cat; else cat -- "$path"; fi > "$CALLS/$n.$i.read"
        i=$((i + 1))
    fi
    prev="$a"
done
exit 0
"""
# The public text gate's check as an agent's call meets it: it reads a --body-file whole, as the shared check does,
# and refuses one that is not a regular file, as the shared check does (`file_ok`).
GATE_STUB = r"""#!/bin/bash
prev=""
for a in "$@"; do
    if [[ "$prev" == --body-file ]]; then
        [[ -f "$a" ]] || { echo "gate stub: $a is not a regular file" >&2; exit 2; }
        cat -- "$a" > "$GATE_READ"
    fi
    prev="$a"
done
exit 0
"""
# A body past a pipe's 64 KiB buffer, so the writer of a process substitution waits until the shim has read part of it,
# and the canary on its last line, so a refusal shows the check read it to the end.
LONG = CLEAN * 4000
LONG_TEXT = LONG + TEXT
LAST_LINE = LONG_TEXT.count("\n")
# Each way a call names a process substitution, as a caller's bash line: (name, line, the field a refusal names, where
# {fd} stands for the descriptor bash chose). $BODY names a file that holds the text, $JSON one with an --input payload
# that carries it: an environment variable holds at most 128 KiB.
PIPE_FORMS = (
    ("pr edit --body-file <(…)", '"$GH" pr edit 11 -R "$REPO" --body-file <(cat -- "$BODY")', "--body-file {fd}"),
    ("pr comment -F <(…)", '"$GH" pr comment 11 -R "$REPO" -F <(cat -- "$BODY")', "--body-file {fd}"),
    ("issue edit --body-file=<(…)", '"$GH" issue edit 4 -R "$REPO" --body-file=<(cat -- "$BODY")', "--body-file {fd}"),
    ("api -F body=@<(…)", '"$GH" api -X PATCH "repos/$REPO/pulls/11" -F body=@<(cat -- "$BODY")', "--field body=@{fd}"),
    ("api -F body=@/dev/fd/3 3< <(…)",
     '"$GH" api -X PATCH "repos/$REPO/pulls/11" -F body=@/dev/fd/3 3< <(cat -- "$BODY")', "--field body=@{fd}"),
    ("api --input <(…)", '"$GH" api -X PATCH "repos/$REPO/pulls/11" --input <(cat -- "$JSON")', "--input {fd} .body"),
)


class GhPipeBody(LeakTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.private_home()
        write_executable(self.root / "bin" / "gh", FAKE_GH)
        self.gate = write_executable(self.root / "gate-stub", GATE_STUB)
        self.gate_read = self.root / "gate-read"
        self.calls = self.root / "calls"
        self.calls.mkdir()
        visibility = self.root / "visibility"
        visibility.mkdir()
        for slug, answer in ((PUBLIC, "public"), (PRIVATE, "private")):
            (visibility / slug.replace("/", "_")).write_text(answer + "\n", encoding="utf-8")
        self.work = self.root / "work"
        self.work.mkdir()
        # The shim runs from outside PATH, so it takes the fake gh first (real_gh in shims/gh).
        path = os.pathsep.join([str(self.root / "bin"), *(entry for entry in os.environ["PATH"].split(os.pathsep)
                                                          if entry and Path(entry).resolve() != SHIMS.resolve())])
        inherited = {name: value for name, value in base_environment().items()
                     if not name.startswith(("GH_", "GIT_SSH", "KNIVES_", "PUBLIC_LEAK_CHECK", "DOTFILES_LEAK_TERMS_GH"))}
        self.person = {
            **inherited,
            **fixture_environment(),
            "PATH": path,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "TMPDIR": str(self.tmpdir),
            "DOTFILES_DIR": str(DOTFILES),
            "KNIVES_GH_BYPASS": "1",
            "AGENT_SHIM_PERSON": "1",
            "CALLS": str(self.calls),
            "VIS_DIR": str(visibility),
            "GH": str(GH),
        }
        self.agent = {**{name: value for name, value in self.person.items() if name != "AGENT_SHIM_PERSON"},
                      "OMP_SESSION_ID": SESSION, "PUBLIC_LEAK_CHECK": str(self.gate), "GATE_READ": str(self.gate_read)}

    def run_line(self, line: str, agent: bool = False, input: bytes | None = None, body: str = "", **extra: str
                 ) -> subprocess.CompletedProcess[bytes]:
        """LINE, a caller's bash line that runs "$GH", with EXTRA in its environment and INPUT on its stdin; $BODY names
        a file that holds BODY, and $JSON one that holds an --input payload carrying it."""
        for old in self.calls.iterdir():
            old.unlink()
        (self.root / "body").write_text(body, encoding="utf-8")
        (self.root / "body.json").write_text(json.dumps({"body": body}), encoding="utf-8")
        return self.launch(["bash", "-c", line], cwd=self.work, input=input, text=False, env=self.environment(
            agent, BODY=str(self.root / "body"), JSON=str(self.root / "body.json"), **extra))

    def gh_calls(self) -> list[tuple[list[str], list[bytes]]]:
        """Each call the fake gh got but visibility reads: its arguments, and the bytes it read from each file."""
        found: list[tuple[list[str], list[bytes]]] = []
        for n in range(len(list(self.calls.glob("*.args")))):
            arguments = (self.calls / f"{n}.args").read_bytes().split(b"\0")[:-1]
            reads: list[bytes] = []
            while (read := self.calls / f"{n}.{len(reads)}.read").exists():
                reads.append(read.read_bytes())
            found.append(([a.decode("utf-8", "surrogateescape") for a in arguments], reads))
        return found

    def assert_sent(self, result: subprocess.CompletedProcess[bytes], *reads: bytes) -> list[str]:
        """The call ran once, and gh read READS, every byte, from the files it names; returns its arguments."""
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][1], list(reads))
        return calls[0][0]

    def assert_refused(self, result: subprocess.CompletedProcess[bytes], field: str, line: int = 1) -> None:
        """The leak-term check refused the call for the canary on LINE of FIELD ({fd} any descriptor), and gh never
        ran."""
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 2, stderr)
        self.assertEqual(self.gh_calls(), [], "gh ran although the text holds a listed term")
        self.assertIn("nothing was sent", stderr)
        place = re.escape(field).replace(re.escape("{fd}"), r"/dev/fd/\d+")
        self.assertRegex(stderr, re.compile(rf'^leak terms: gh [^\n]*: {place} line {line}: "{re.escape(CANARY.upper())}"',
                                            re.MULTILINE))

    # --- the cases ------------------------------------------------------------------------------------------------

    def test_a_process_substitution_reaches_gh_whole_after_the_check_reads_it(self) -> None:
        # A public repository: the leak-term check reads the text before the call may go, and gh then gets all of it.
        for name, line, _ in PIPE_FORMS:
            with self.subTest(form=name):
                result = self.run_line(line, body=LONG, REPO=PUBLIC)
                sent = json.dumps({"body": LONG}) if "--input" in name else LONG
                arguments = self.assert_sent(result, sent.encode())
                self.assertTrue(any(re.search(r"/dev/fd/\d+$", argument) for argument in arguments), arguments)

    def test_a_listed_term_in_a_process_substitution_is_refused(self) -> None:
        for name, line, field in PIPE_FORMS:
            with self.subTest(form=name):
                # The term on the last line of a body longer than a pipe holds: the check read it to the end.
                body = TEXT if "--input" in name else LONG_TEXT
                result = self.run_line(line, body=body, REPO=PUBLIC)
                self.assert_refused(result, field, 1 if "--input" in name else LAST_LINE)
                # Control: the same text goes to a private repository whole.
                result = self.run_line(line, body=body, REPO=PRIVATE)
                self.assert_sent(result, (json.dumps({"body": body}) if "--input" in name else body).encode())

    def test_a_regular_file_and_standard_input_still_reach_gh(self) -> None:
        (self.work / "body.md").write_text(LONG, encoding="utf-8")
        self.assert_sent(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file body.md', REPO=PUBLIC), LONG.encode())
        self.assert_sent(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file -', input=LONG.encode(), REPO=PUBLIC),
                         LONG.encode())
        self.assert_sent(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file /dev/stdin', input=LONG.encode(),
                                       REPO=PUBLIC), LONG.encode())
        (self.work / "body.md").write_text(LONG_TEXT, encoding="utf-8")
        self.assert_refused(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file body.md', REPO=PUBLIC),
                            "--body-file body.md", LAST_LINE)
        self.assert_refused(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file -', input=LONG_TEXT.encode(),
                                          REPO=PUBLIC), "--body-file - (standard input)", LAST_LINE)
        self.assert_refused(self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file /dev/stdin',
                                          input=LONG_TEXT.encode(), REPO=PUBLIC), "--body-file /dev/stdin", LAST_LINE)

    def test_an_agent_s_public_text_gate_reads_the_bytes_gh_then_gets(self) -> None:
        # The gate stub refuses a --body-file that is not a regular file, as the shared check does.
        for name, line, stdin in (("<(…)", '"$GH" pr edit 11 -R "$REPO" --body-file <(cat -- "$BODY")', None),
                                  ("/dev/stdin", '"$GH" pr edit 11 -R "$REPO" --body-file /dev/stdin', LONG.encode())):
            with self.subTest(form=name):
                self.gate_read.unlink(missing_ok=True)
                self.assert_sent(self.run_line(line, agent=True, input=stdin, body=LONG, REPO=PRIVATE), LONG.encode())
                self.assertEqual(self.gate_read.read_bytes(), LONG.encode())

    def test_a_stream_past_64_mib_is_refused_and_named(self) -> None:
        result = self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file <(head -c 67108865 /dev/zero)', REPO=PRIVATE)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 1, stderr)
        self.assertRegex(stderr, r"gh shim: refusing: /dev/fd/\d+ is over 64 MiB")
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(list(self.tmpdir.iterdir()), [], "the shim left its buffer behind")

    def test_a_descriptor_gh_writes_is_left_to_gh(self) -> None:
        # `-O >(…)` names the write end of a pipe: the shim reads nothing from it, and gh writes the asset there.
        out = self.root / "asset"
        line = '"$GH" release download v1 -R "$REPO" -O >(cat > "$OUT"); status=$?; wait $!; exit $status'
        result = self.run_line(line, REPO=PRIVATE, OUT=str(out))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(out.read_bytes(), b"asset\n")

    def test_a_fifo_named_by_its_own_path_is_left_to_the_check(self) -> None:
        # mkfifo's pipe has a path of its own, which the shim cannot point at a file: the check does not read it, and
        # refuses its text for a public repository. Nothing opens the pipe, so no writer is needed.
        os.mkfifo(self.work / "pipe")
        result = self.run_line('"$GH" pr edit 11 -R "$REPO" --body-file pipe', REPO=PUBLIC)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 2, stderr)
        self.assertIn("cannot read --body-file pipe: a pipe or a device, which the check cannot read", stderr)
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()
