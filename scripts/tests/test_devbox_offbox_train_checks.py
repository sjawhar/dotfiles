"""The merge train's gate lock and its three standing processes, watched from off the box.

Until 2026-11-09 five train facts were checked by a job armed ON sami-devbox
(`operator/train_alive.sh`, the `merge-train-alive` stream job): runner.lock's pid, gate.lock's
pid, and a process for each of `operator/revert_watch.py`, `operator/nightly_accept_load.py` and
`operator/snapshot.sh --loop`. A watcher that lives on the machine it watches dies with it --
the whole lesson of 2026-11-04, when sami-devbox stalled for 85 minutes and every watcher on it
went quiet together (dispatch://INFRA-993/ask/d13b86a5) -- and agent timers on that box are
going (dispatch://INFRA-2022/ask/4badbed6). The off-box watch already had runner.lock and the
events.tsv stall; this file holds the other four.

Three states per fact, never two. `running`, `gone`, and `unreadable` -- the probe could not
look: no procfs, an unparseable or missing gate.lock, a train directory it cannot read. An
unreadable fact is its own alert, because the one failure a liveness check must never have is
reporting "all alive" when it could not see.

The cluster CronJob's transport is checked too (`devbox-offbox-act --forced probe`), since the
sami-rocqperf timer may end under the same ruling and the pod is then the only caller.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
PROBE = SCRIPTS / "devbox-offbox-probe"
ACT = SCRIPTS / "devbox-offbox-act"
WATCH_PATH = SCRIPTS / "devbox-offbox-watch"

OPERATOR = "01a1a292-7729-762c-abab-5fc4b597f94a"
PATTERNS = {
    "revert_watch": "/srv/train/operator/revert_watch.py",
    "nightly_accept_load": "python3 /srv/train/operator/nightly_accept_load.py --once",
    "snapshot_loop": "bash /srv/train/operator/snapshot.sh --loop",
}


def load_watch():
    loader = importlib.machinery.SourceFileLoader("devbox_offbox_watch", str(WATCH_PATH))
    spec = importlib.util.spec_from_loader("devbox_offbox_watch", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = load_watch()


# --- the probe ---------------------------------------------------------------------------------


def make_proc(root: Path, processes: dict[int, str]) -> Path:
    """A private procfs: one directory per pid, each with the NUL-separated cmdline the kernel
    writes. The writeback half of this probe is driven the same way, so no test ever needs the
    real process table -- and that is why the scan reads <proc-root> rather than calling pgrep,
    which always reads /proc."""
    root.mkdir(parents=True, exist_ok=True)
    for pid, command in processes.items():
        directory = root / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / "cmdline").write_bytes(b"\0".join(w.encode() for w in command.split()) + b"\0")
    return root


def make_train(root: Path, *, gate: str | None = "pid 4101 since 2026-11-09T01:00:00Z") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runner.lock").write_text(
        f"pid 4100 host sami-devbox since 2026-11-09T01:00:00Z agent {OPERATOR}\n")
    if gate is not None:
        (root / "gate.lock").write_text(gate + "\n")
    return root


def probe(proc: Path, train: Path, registry: Path | str = "/nonexistent") -> dict[str, str]:
    environment = dict(os.environ, DEVBOX_OFFBOX_HEALTH_PROBE="/nonexistent")
    done = subprocess.run(["bash", str(PROBE), str(proc), str(registry), str(train)],
                          capture_output=True, text=True, timeout=120, env=environment)
    assert done.returncode == 0, done.stderr
    facts = {}
    for line in done.stdout.splitlines():
        if line.startswith("===") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        facts[key] = value
    return facts


def test_the_probe_reports_a_live_gate_and_three_running_processes(tmp_path) -> None:
    proc = make_proc(tmp_path / "proc", {4100: "train.sh run", 4101: "gate.sh",
                                         **{4200 + i: command
                                            for i, command in enumerate(PATTERNS.values())}})
    facts = probe(proc, make_train(tmp_path / "train"))
    assert facts["train_present"] == "yes"
    assert facts["train_gate_pid"] == "4101"
    assert facts["train_gate_pid_alive"] == "yes"
    assert facts["train_gate_lock"].startswith("pid 4101 ")
    for name in PATTERNS:
        assert facts[f"train_proc_{name}"] == "yes", name


def test_the_probe_reports_a_dead_gate_pid(tmp_path) -> None:
    proc = make_proc(tmp_path / "proc", {4100: "train.sh run"})
    facts = probe(proc, make_train(tmp_path / "train"))
    assert facts["train_gate_pid_alive"] == "no"
    assert facts["train_lock_pid_alive"] == "yes"


@pytest.mark.parametrize("gate", [None, "", "the lock format changed"])
def test_a_missing_or_unparseable_gate_lock_is_unreadable_not_dead(tmp_path, gate) -> None:
    """The distinction the whole check turns on: nothing was learned, so nothing is claimed."""
    proc = make_proc(tmp_path / "proc", {4100: "train.sh run"})
    facts = probe(proc, make_train(tmp_path / "train", gate=gate))
    assert facts["train_gate_pid_alive"] == "unreadable"


def test_an_absent_process_is_no_and_the_others_stay_yes(tmp_path) -> None:
    running = {4200 + i: command for i, command in enumerate(PATTERNS.values())
               if command != PATTERNS["snapshot_loop"]}
    proc = make_proc(tmp_path / "proc", {4100: "train.sh run", 4101: "gate.sh", **running})
    facts = probe(proc, make_train(tmp_path / "train"))
    assert facts["train_proc_snapshot_loop"] == "no"
    assert facts["train_proc_revert_watch"] == "yes"
    assert facts["train_proc_nightly_accept_load"] == "yes"


def test_an_unreadable_procfs_makes_every_process_unreadable(tmp_path) -> None:
    """No process table, no answer -- and the answer is not `no` for all three, which would
    have read as "the whole train is down" and sent somebody to restart running processes."""
    facts = probe(tmp_path / "there-is-no-procfs", make_train(tmp_path / "train"))
    for name in PATTERNS:
        assert facts[f"train_proc_{name}"] == "unreadable", name


def test_the_scan_does_not_match_itself(tmp_path) -> None:
    """The bracketed patterns of train_alive.sh existed so pgrep could not match its own command
    line. The awk scan must not match its own either -- it holds the three patterns in argv."""
    proc = make_proc(tmp_path / "proc", {4100: "train.sh run"})
    facts = probe(proc, make_train(tmp_path / "train"))
    assert [facts[f"train_proc_{name}"] for name in PATTERNS] == ["no", "no", "no"]


def test_an_unreadable_train_directory_says_so(tmp_path) -> None:
    train = make_train(tmp_path / "train")
    train.chmod(0o000)
    try:
        facts = probe(make_proc(tmp_path / "proc", {4100: "train.sh run"}), train)
    finally:
        train.chmod(0o755)
    assert facts["train_present"] == "unreadable"


def test_no_train_directory_at_all_is_not_an_alert(tmp_path) -> None:
    facts = probe(make_proc(tmp_path / "proc", {4100: "x"}), tmp_path / "nothing-here")
    assert facts["train_present"] == "no"


# --- the forced command, which is how the cluster CronJob asks --------------------------------


def test_the_forced_probe_carries_the_new_facts(tmp_path) -> None:
    """From the dispatch cluster the pod's key is `restrict,command="... --forced"`, so `probe`
    is a verb and not a script over stdin. The new facts must reach that caller too."""
    train = make_train(tmp_path / "train")
    environment = dict(os.environ,
                       SSH_ORIGINAL_COMMAND="probe",
                       HOME=str(tmp_path / "home"),
                       DEVBOX_OFFBOX_REGISTRY=str(tmp_path / "registry"),
                       DEVBOX_OFFBOX_TRAIN=str(train),
                       DEVBOX_OFFBOX_HEALTH_PROBE="/nonexistent")
    (tmp_path / "home").mkdir(exist_ok=True)
    done = subprocess.run([str(ACT), "--forced"], capture_output=True, text=True, timeout=120,
                          env=environment)
    assert done.returncode == 0, done.stderr
    facts = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    assert facts["train_present"] == "yes"
    assert facts["train_gate_pid_alive"] in {"yes", "no", "unreadable"}
    for name in PATTERNS:
        assert facts[f"train_proc_{name}"] in {"yes", "no", "unreadable"}, name


# --- the watch's decisions ---------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, targets, kind, text) -> bool:
        self.sent.append((kind, text))
        return True


def facts_for(**over: str) -> dict[str, str]:
    base = {
        "train_present": "yes",
        "train_lock": f"pid 4100 since 2026-11-09T01:00:00Z agent {OPERATOR}",
        "train_lock_agent": OPERATOR,
        "train_lock_pid_alive": "yes",
        "train_gate_lock": "pid 4101 since 2026-11-09T01:00:00Z",
        "train_gate_pid_alive": "yes",
        "train_queue_open": "2",
        "train_events_mtime": "",
    }
    base.update({f"train_proc_{name}": "yes" for name in PATTERNS})
    base.update(over)
    return base


def passes(*facts: dict[str, str]) -> tuple[list[tuple[str, str]], dict]:
    """Run consecutive passes of check_train over one state file, as the timer would."""
    state = {"train": {}}
    report = Recorder()
    for fact in facts:
        watch.check_train(fact, state, report, 1.0e9, "/srv/train", "SRE", "VLLEAD")
    return report.sent, state


def kinds(sent) -> list[str]:
    return [kind for kind, _ in sent]


def test_a_dead_gate_pid_alerts_once_and_clears(tmp_path) -> None:
    dead = facts_for(train_gate_pid_alive="no")
    sent, _ = passes(dead, dead, dead, facts_for())
    assert kinds(sent) == ["train-gate-gone", "train-gate-back"], sent
    assert "gate.lock" in sent[0][1] and "agents:* tmux window" in sent[0][1]
    assert "HANDOFF.md" in sent[0][1]


def test_an_unreadable_gate_is_its_own_alert_and_never_an_all_clear() -> None:
    sent, _ = passes(facts_for(train_gate_pid_alive="unreadable"),
                     facts_for(train_gate_pid_alive="unreadable"))
    assert kinds(sent) == ["train-unreadable"], sent
    assert "gate.lock" in sent[0][1] and "not alive, not dead" in sent[0][1]


def test_a_missing_process_alerts_once_names_the_set_and_clears() -> None:
    down = facts_for(train_proc_snapshot_loop="no")
    sent, _ = passes(down, down, facts_for())
    assert kinds(sent) == ["train-procs-gone", "train-procs-back"], sent
    assert "operator/snapshot.sh --loop" in sent[0][1]
    assert "agents:* tmux window" in sent[0][1] and "starts nothing" in sent[0][1]
    assert "operator/snapshot.sh --loop" in sent[1][1]


def test_a_growing_missing_set_is_a_new_fact() -> None:
    one = facts_for(train_proc_snapshot_loop="no")
    two = facts_for(train_proc_snapshot_loop="no", train_proc_revert_watch="no")
    sent, _ = passes(one, one, two, two, facts_for())
    assert kinds(sent) == ["train-procs-gone", "train-procs-gone", "train-procs-back"], sent
    assert "operator/revert_watch.py" in sent[1][1]


def test_an_unreadable_process_reading_is_not_a_missing_process() -> None:
    sent, _ = passes(facts_for(train_proc_revert_watch="unreadable"))
    assert kinds(sent) == ["train-unreadable"], sent
    assert "operator/revert_watch.py" in sent[0][1]


def test_all_five_alive_says_nothing() -> None:
    sent, _ = passes(facts_for(), facts_for())
    assert sent == []


def test_an_unreadable_train_directory_alerts_once_and_checks_nothing_else() -> None:
    sent, _ = passes({"train_present": "unreadable"}, {"train_present": "unreadable"})
    assert kinds(sent) == ["train-unreadable"], sent
    assert "NOTHING about the train was checked" in sent[0][1]


def test_no_train_directory_is_silence() -> None:
    sent, _ = passes({"train_present": "no"})
    assert sent == []


def test_the_runner_check_still_works_beside_the_new_ones() -> None:
    sent, _ = passes(facts_for(train_lock_pid_alive="no", train_gate_pid_alive="no"))
    assert kinds(sent) == ["train-runner-gone", "train-gate-gone"], sent
