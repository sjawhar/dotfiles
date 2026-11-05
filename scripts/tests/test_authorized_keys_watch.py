"""authorized-keys-watch says when a login key appears, and never says the key.

On 2026-11-04 at 02:58Z an agent ran a Google Cloud command that added a login key to
sami-devbox and nothing told anybody. The deploy now declares the login list, so an added key
is undone at the next apply (agent-infra #2151) -- but undone at the next apply is not
noticed, and between applies that key works. This watch is the noticing half.

Two properties matter more than the diffing, and both are tested here: the key material never
reaches the state file, the message or the output, and the first run alerts nobody, because a
watch that reports every existing key as new on its first pass teaches its reader to skip it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

WATCH = Path(__file__).resolve().parent.parent / "authorized-keys-watch"


def keypair(tmp_path: Path, name: str, comment: str) -> str:
    path = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
                   check=True, timeout=60)
    return (tmp_path / f"{name}.pub").read_text().strip()


def run(keys: Path, state: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(WATCH), "--file", str(keys), "--state", str(state), "--print", "--dry-run", *extra],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )


@pytest.fixture
def setup(tmp_path):
    alice = keypair(tmp_path, "alice", "alice@laptop")
    mallory = keypair(tmp_path, "mallory", "mallory@added")
    keys = tmp_path / "authorized_keys"
    keys.write_text(alice + "\n")
    return {"dir": tmp_path, "keys": keys, "state": tmp_path / "state.json",
            "alice": alice, "mallory": mallory}


def settle(setup) -> None:
    """Record the current keys so the next pass has something to compare against.

    `--no-send` rather than a plain run: a plain run of this helper on a changed file sends a
    real alert to the box-health role, and writing these tests did exactly that from a
    directory under /tmp/pytest. A test must not reach the fleet to set up its own state.
    """
    subprocess.run([str(WATCH), "--file", str(setup["keys"]), "--state", str(setup["state"]),
                    "--no-send"],
                   check=True, capture_output=True, text=True, timeout=120)


def test_the_first_run_reports_no_change(setup):
    done = run(setup["keys"], setup["state"])
    assert done.returncode == 0
    assert "first run" in done.stdout
    assert "CHANGED" not in done.stdout


def test_an_unchanged_file_says_so_and_nothing_else(setup):
    settle(setup)
    done = run(setup["keys"], setup["state"])
    assert "unchanged" in done.stdout
    assert "CHANGED" not in done.stdout


def test_an_added_key_is_reported_with_its_fingerprint_and_comment(setup):
    settle(setup)
    setup["keys"].write_text(setup["alice"] + "\n" + setup["mallory"] + "\n")
    done = run(setup["keys"], setup["state"])
    assert "CHANGED" in done.stdout
    assert "ADDED" in done.stdout
    assert "mallory@added" in done.stdout
    assert "SHA256:" in done.stdout


def test_a_removed_key_is_reported_too(setup):
    """A key going away is as worth knowing as one arriving: it may be somebody locked out."""
    setup["keys"].write_text(setup["alice"] + "\n" + setup["mallory"] + "\n")
    settle(setup)
    setup["keys"].write_text(setup["alice"] + "\n")
    done = run(setup["keys"], setup["state"])
    assert "REMOVED" in done.stdout
    assert "mallory@added" in done.stdout


def test_no_key_material_reaches_the_output_or_the_state(setup):
    settle(setup)
    setup["keys"].write_text(setup["alice"] + "\n" + setup["mallory"] + "\n")
    done = run(setup["keys"], setup["state"])
    body = setup["mallory"].split()[1]
    assert body not in done.stdout and body not in done.stderr
    subprocess.run([str(WATCH), "--file", str(setup["keys"]), "--state", str(setup["state"]),
                    "--no-send"],
                   check=True, capture_output=True, text=True, timeout=120)
    written = setup["state"].read_text()
    assert body not in written
    assert "SHA256:" in written


def test_a_comment_is_kept_because_it_is_how_a_person_recognises_a_key(setup):
    settle(setup)
    setup["keys"].write_text(setup["alice"] + "\n" + setup["mallory"] + "\n")
    assert "mallory@added" in run(setup["keys"], setup["state"]).stdout


def test_an_unreadable_state_file_reports_nothing_rather_than_everything(setup):
    """A corrupt state must not read as "every key was just added"."""
    settle(setup)
    setup["state"].write_text("{not json")
    done = run(setup["keys"], setup["state"])
    assert done.returncode == 0
    assert "CHANGED" not in done.stdout
    assert "could not be read" in done.stderr


def test_a_comment_line_is_not_a_key(setup):
    settle(setup)
    setup["keys"].write_text("# a comment the deploy writes\n" + setup["alice"] + "\n")
    done = run(setup["keys"], setup["state"])
    assert "unchanged" in done.stdout


def test_the_state_file_is_private(setup):
    settle(setup)
    assert oct(setup["state"].stat().st_mode)[-3:] == "600"


def test_the_state_holds_one_entry_per_key(setup):
    setup["keys"].write_text(setup["alice"] + "\n" + setup["mallory"] + "\n")
    settle(setup)
    recorded = json.loads(setup["state"].read_text())
    assert sum(len(v) for v in recorded.values()) == 2
