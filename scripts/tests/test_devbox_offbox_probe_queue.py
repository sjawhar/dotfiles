"""The merge-train stall alert counts work, not finished rows.

devbox-offbox-watch tells the train's operator when "the queue holds work and events.tsv has
not moved for 30 minutes". devbox-offbox-probe counted a row as work unless it was landed,
dropped or returned -- leaving out two more terminal states the train uses, withdrawn and
superseded. On 2026-11-05 an idle train with 51 withdrawn and 204 superseded rows read as 255
open items, and the 04:38Z and 05:22Z passes both sent MERGE TRAIN STUCK for a train whose
runner was alive, polling and simply out of work.

That matters beyond the noise: the same watch's other half correctly caught a dead runner an
hour earlier, and an alert that fires every time a queue drains teaches its readers to ignore
both.

The count is "not in a terminal state" rather than "in a work state" on purpose, so a state
nobody has heard of still raises the alert instead of being dropped in silence.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parent.parent / "devbox-offbox-probe"
HEADER = "id\tbranch\tsha\tagent\tat\tdetail\tstate\n"


def counter() -> str:
    """The probe's own awk program for train_queue_open, cut out of the script."""
    text = PROBE.read_text()
    match = re.search(r"printf 'train_queue_open=%s\\n' \"\$\(awk (.*?)\"\$train/queue\.tsv\"\)\"",
                      text, re.DOTALL)
    assert match, "the train_queue_open counter is no longer findable in devbox-offbox-probe"
    return match.group(1)


def count(tmp_path: Path, states: list[str]) -> int:
    queue = tmp_path / "queue.tsv"
    rows = "".join(f"{i}\tbranch\tsha\tagent\tat\tdetail\t{s}\n" for i, s in enumerate(states))
    queue.write_text(HEADER + rows)
    done = subprocess.run(["bash", "-c", f'train={tmp_path!s}; awk {counter()}"$train/queue.tsv"'],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return int(done.stdout.strip())


@pytest.mark.parametrize("state", ["landed", "dropped", "returned", "withdrawn", "superseded"])
def test_a_terminal_state_is_not_work(tmp_path, state):
    assert count(tmp_path, [state] * 7) == 0


@pytest.mark.parametrize("state", ["queued", "resolving", "testing"])
def test_a_work_state_is_work(tmp_path, state):
    assert count(tmp_path, [state]) == 1


def test_the_idle_train_of_2026_11_05_holds_no_work(tmp_path):
    """The exact queue that raised two false alarms: every row terminal."""
    states = ["landed"] * 206 + ["superseded"] * 204 + ["withdrawn"] * 51 + ["returned"] * 81
    assert count(tmp_path, states) == 0


def test_one_queued_row_among_the_finished_ones_is_still_work(tmp_path):
    states = ["landed"] * 206 + ["superseded"] * 204 + ["withdrawn"] * 51 + ["queued"]
    assert count(tmp_path, states) == 1


def test_an_unknown_state_counts_as_work(tmp_path):
    """Better a question than a silence: a state nobody listed still raises the alert."""
    assert count(tmp_path, ["landed", "something-new"]) == 1


def test_an_empty_queue_holds_no_work(tmp_path):
    assert count(tmp_path, []) == 0
