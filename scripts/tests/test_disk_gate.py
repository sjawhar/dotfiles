#!/usr/bin/env python3
"""Regression tests for disk-gate's accounting of reserved bytes.

`df` already shows what a holder has written, so a reservation counted at its full size
for its whole life subtracts those bytes twice. The gate counts only the unwritten part of
a reservation, lets a holder renew downward at any time, keeps a holder's reservation when
an upward renewal is refused, and counts a holder it cannot measure at its full size. It
measures what the holder's process tree wrote on the mount from /proc/<pid>/io, so a lane
need not report it; `--written` overrides the measurement. The numbers are LANE-C's
refusal on /mnt/nvme at 2026-11-02T16:21Z: 156.8 GiB free, holders of 25+40+4+2+1 GiB,
78 GiB floor.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


def fs_type(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)], capture_output=True, text=True).stdout.strip()


def other_disk(than: Path) -> Path | None:
    """A writable directory on a disk-backed file system other than `than`'s, if this box has one."""
    for candidate in (os.environ.get("DISK_GATE_TEST_OTHER_DISK"), "/mnt/nvme/sami/scratch", "/var/tmp"):
        if not candidate or not os.path.isdir(candidate) or not os.access(candidate, os.W_OK):
            continue
        if os.stat(candidate).st_dev != os.stat(than).st_dev and fs_type(Path(candidate)) not in ("tmpfs", "ramfs"):
            return Path(candidate)
    return None


SCRIPT = Path(__file__).resolve().parents[1] / "disk-gate"

# The filesystem's size answers `--output=size` (the gate scales /mnt/nvme's floor by it); any
# other call is the free space.
FAKE_DF = """#!/usr/bin/env bash
if [[ " $* " == *" --output=size "* ]]; then
    echo 1B-blocks
    awk -v g="${FAKE_SIZE_GIB:-3464}" 'BEGIN {printf "%d\\n", g * 1073741824}'
    exit
fi
echo Avail
awk -v g="$FAKE_FREE_GIB" 'BEGIN {printf "%d\\n", g * 1073741824}'
"""

# The holders when LANE-C was refused at 16:21Z; /mnt/nvme's floor is 78 GiB.
HOLDERS_1621 = [
    ("FR15Grade", 25),
    ("o16-gt2f91-build", 40),
    ("offer-findings-check", 4),
    ("ks6-stage-pins", 2),
    ("exitguard16-recipes-build13", 1),
]
FREE_1621 = 156.8


class DiskGateAccountingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        df = bin_dir / "df"
        df.write_text(FAKE_DF)
        df.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", DISK_GATE_STATE=str(self.state))
        self.env.pop("DISK_GATE_TTL", None)
        self.ledger = self.state / "_mnt_nvme.tsv"

    def seed(self, holders: list[tuple[str, int]]) -> None:
        """Rows as the gate before this change wrote them: name, GiB, epoch seconds."""
        now = int(time.time())
        self.ledger.write_text("".join(f"{name}\t{gib}\t{now}\n" for name, gib in holders))

    def gate(self, *args: str, free: float = FREE_1621, size: float = 3464) -> subprocess.CompletedProcess:
        env = dict(self.env, FAKE_FREE_GIB=str(free), FAKE_SIZE_GIB=str(size))
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)

    def status_line(self, free: float = FREE_1621) -> str:
        result = self.gate("status", free=free)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()[0]

    def rows(self) -> dict[str, list[str]]:
        return {line.split("\t")[0]: line.split("\t")[1:] for line in self.ledger.read_text().splitlines()}

    def test_silent_holders_are_counted_in_full_as_before(self) -> None:
        self.seed(HOLDERS_1621)
        self.assertEqual(
            self.status_line(),
            "/mnt/nvme: 156.8 GiB free, 72 GiB reserved, 78 GiB floor, 6.8 GiB available to reserve.",
        )
        refused = self.gate("reserve", "7", "lanec-check")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("your 7 GiB would leave 77.8 GiB, under the 78 GiB floor", refused.stdout)
        self.assertNotIn("lanec-check", self.rows())

    def test_written_bytes_are_not_counted_twice(self) -> None:
        """LANE-C's 16:21Z case: the 40 GiB build has written 30 GiB, which df already shows."""
        self.seed(HOLDERS_1621)
        renewed = self.gate("renew", "o16-gt2f91-build", "--written", "30")
        self.assertEqual(renewed.returncode, 0, renewed.stderr)
        self.assertIn("counts 10 GiB (40 reserved, 30 written)", renewed.stdout)
        self.assertEqual(
            self.status_line(),
            "/mnt/nvme: 156.8 GiB free, 42 GiB reserved, 78 GiB floor, 36.8 GiB available to reserve.",
        )
        self.assertIn("o16-gt2f91-build 40 GiB since", self.gate("status").stdout)
        self.assertIn("30 GiB written, 10 GiB still counted", self.gate("status").stdout)
        go = self.gate("reserve", "7", "lanec-check")
        self.assertEqual(go.returncode, 0, go.stdout)
        self.assertIn("29.8 GiB over the 78 GiB floor", go.stdout)

    def test_holder_that_wrote_all_its_reservation_costs_nothing(self) -> None:
        self.seed(HOLDERS_1621)
        self.assertEqual(self.gate("renew", "o16-gt2f91-build", "--written", "40").returncode, 0)
        self.assertIn("32 GiB reserved", self.status_line())
        # Writing past the reservation does not make the others' count go negative.
        self.assertEqual(self.gate("renew", "o16-gt2f91-build", "--written", "55").returncode, 0)
        self.assertIn("32 GiB reserved, 78 GiB floor, 46.8 GiB available", self.status_line())

    def test_holder_that_wrote_nothing_costs_its_full_size(self) -> None:
        self.seed(HOLDERS_1621)
        self.assertEqual(self.gate("renew", "o16-gt2f91-build", "--written", "0").returncode, 0)
        self.assertIn("72 GiB reserved", self.status_line())
        self.assertEqual(self.gate("reserve", "10", "fresh-lane", free=200).returncode, 0)
        self.assertIn("82 GiB reserved", self.status_line(free=200))

    def test_reported_written_survives_a_plain_reserve_repeat(self) -> None:
        """A lane renews with its old `reserve <gib> <name>` line; its report must not be lost."""
        self.seed(HOLDERS_1621)
        self.gate("reserve", "40", "o16-gt2f91-build", "--written", "30")
        repeat = self.gate("reserve", "40", "o16-gt2f91-build", free=100)
        self.assertEqual(repeat.returncode, 0, repeat.stdout)
        self.assertEqual([self.rows()["o16-gt2f91-build"][i] for i in (0, 2)], ["40", "30"])

    def test_downward_renewal_is_never_refused(self) -> None:
        self.seed(HOLDERS_1621)
        # 90 GiB free is under what the holders need: any growth would be refused.
        down = self.gate("renew", "o16-gt2f91-build", "15", free=90)
        self.assertEqual(down.returncode, 0, down.stdout + down.stderr)
        self.assertIn("RENEWED. o16-gt2f91-build on /mnt/nvme counts 15 GiB", down.stdout)
        self.assertEqual(self.rows()["o16-gt2f91-build"][0], "15")
        old_style = self.gate("reserve", "20", "FR15Grade", free=90)
        self.assertEqual(old_style.returncode, 0, old_style.stdout)
        self.assertIn("renewed; it was 25 GiB", old_style.stdout)
        self.assertIn("42 GiB reserved", self.status_line(free=90))

    def test_floor_still_refuses_new_and_upward_reservations(self) -> None:
        self.seed(HOLDERS_1621)
        self.gate("renew", "o16-gt2f91-build", "--written", "30")
        # 36.8 GiB is available: 37 must be refused, 36 granted.
        refused = self.gate("reserve", "37", "big-lane")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("42 GiB is reserved by other lanes and not yet written", refused.stdout)
        self.assertNotIn("big-lane", self.rows())
        # An upward renewal is checked as a new reservation, and a refusal keeps the old row.
        up = self.gate("reserve", "70", "FR15Grade")
        self.assertEqual(up.returncode, 1)
        self.assertIn("you keep your 25 GiB reservation as it was", up.stdout)
        self.assertEqual(self.rows()["FR15Grade"][0], "25")
        up_renew = self.gate("renew", "FR15Grade", "70")
        self.assertEqual(up_renew.returncode, 1)
        self.assertEqual(self.rows()["FR15Grade"][0], "25")
        # Reporting less written raises the cost, so it is checked too.
        less = self.gate("renew", "o16-gt2f91-build", "--written", "0", free=100)
        self.assertEqual(less.returncode, 1)
        self.assertEqual(self.rows()["o16-gt2f91-build"][2], "30")
        self.assertEqual(self.gate("reserve", "36", "big-lane").returncode, 0)

    def test_renew_finds_the_name_on_its_mount_and_refuses_unknown_names(self) -> None:
        self.assertEqual(self.gate("reserve", "5", "durable-lane", "--mount", "/mnt/durable").returncode, 0)
        renewed = self.gate("renew", "durable-lane", "--written", "2")
        self.assertEqual(renewed.returncode, 0, renewed.stderr)
        self.assertIn("on /mnt/durable counts 3 GiB", renewed.stdout)
        unknown = self.gate("renew", "nobody")
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("NOT RENEWED", unknown.stderr)
        self.assertEqual(self.gate("renew", "nobody", "--mount", "/mnt/nvme").returncode, 1)

    def test_current_command_lines_work_unchanged(self) -> None:
        go = self.gate("reserve", "7", "lane", "--mount", "/mnt/nvme")
        self.assertEqual(go.returncode, 0, go.stdout)
        self.assertEqual(self.rows()["lane"][0], "7")
        self.assertEqual(self.rows()["lane"][2], "", "a lane that reports nothing has no reported figure")
        self.assertEqual(self.rows()["lane"][3], str(os.getpid()), "the gate records its caller to measure")
        self.assertIn("7 GiB reserved", self.status_line())
        released = self.gate("release", "lane")
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(self.gate("release", "lane").returncode, 1)
        self.assertEqual(self.gate("reserve", "x", "lane").returncode, 2)
        self.assertEqual(self.gate("reserve", "7", "lane", "--written", "lots").returncode, 2)

    def test_nvme_floor_scales_with_a_disk_larger_than_rocqperfs(self) -> None:
        # sami-rocqperf's 3,464 GiB array keeps 78 GiB; a smaller disk keeps 78 GiB too.
        self.assertIn("78 GiB floor", self.gate("status", size=3464).stdout)
        self.assertIn("78 GiB floor", self.gate("status", size=500).stdout)
        # sami-devbox's 16,383 GiB state disk keeps 2.25% of itself, rounded up: 369 GiB.
        status = self.gate("status", free=9061, size=16383)
        self.assertIn("9061.0 GiB free, 0 GiB reserved, 369 GiB floor, 8692.0 GiB available", status.stdout)
        refused = self.gate("reserve", "8693", "too-big", free=9061, size=16383)
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("under the 369 GiB floor", refused.stdout)
        self.assertEqual(self.gate("reserve", "8692", "fits", free=9061, size=16383).returncode, 0)


class DiskGateMeasurementTest(unittest.TestCase):
    """A lane that never reports `--written` is discounted by what its process tree wrote."""

    MIB = 512  # what the holder writes: 0.5 GiB

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        if fs_type(root) in ("tmpfs", "ramfs"):
            self.skipTest(f"{root} is {fs_type(root)}, which /proc/<pid>/io write_bytes does not count")
        self.state = root / "state"
        self.state.mkdir()
        # The reservation's mount is this directory: the fake df reports FAKE_FREE_GIB for it.
        self.mount = root / "disk"
        self.mount.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "df").write_text(FAKE_DF)
        (bin_dir / "df").chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", DISK_GATE_STATE=str(self.state))
        self.env.pop("DISK_GATE_TTL", None)
        self.env.pop("PI_SESSION_ID", None)
        self.holders: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for holder in self.holders:
            try:
                os.killpg(holder.pid, 9)
            except ProcessLookupError:
                pass
            holder.wait()

    def gate(self, *args: str, free: float) -> subprocess.CompletedProcess:
        env = dict(self.env, FAKE_FREE_GIB=str(free))
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)

    def wait_for(self, path: Path, holder: subprocess.Popen) -> None:
        deadline = time.time() + 30
        while not path.exists():
            self.assertIsNone(holder.poll(), f"holder exited early: {(self.mount.parent / 'holder.log').read_text()}")
            self.assertLess(time.time(), deadline, f"no {path.name}")
            time.sleep(0.05)

    def holder(
        self,
        mount: Path,
        cwd: Path,
        write: str,
        *,
        free: float,
        stay: bool = True,
        reserve_env: str = "",
        names: tuple[str, ...] = ("auto-lane",),
    ) -> subprocess.Popen:
        """A lane as lanes run today: one `bash -c` reserves, writes, and never reports.

        `write` is the shell that writes; $N is the bytes to write.
        """
        flags = self.mount.parent
        reserves = "".join(f'{reserve_env} "{SCRIPT}" reserve 1 {name} --mount "{mount}" || exit 1; ' for name in names)
        script = (
            f'{reserves}touch "{flags}/reserved"; '
            f'while [ ! -e "{flags}/go" ]; do sleep 0.05; done; N=$(({self.MIB} * 1048576)); {write}; '
            f'touch "{flags}/wrote"; ' + ("sleep 120" if stay else "exit 0")
        )
        log = open(flags / "holder.log", "w")
        self.addCleanup(log.close)
        holder = subprocess.Popen(
            ["bash", "-c", script],
            cwd=cwd,
            env=dict(self.env, FAKE_FREE_GIB=str(free)),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        self.holders.append(holder)
        self.wait_for(flags / "reserved", holder)
        return holder

    def write(self, holder: subprocess.Popen) -> None:
        (self.mount.parent / "go").touch()
        deadline = time.time() + 60
        while not (self.mount.parent / "wrote").exists():
            self.assertLess(time.time(), deadline, "the holder did not write")
            time.sleep(0.05)

    def status(self, mount: Path, free: float) -> str:
        result = self.gate("status", "--mount", str(mount), free=free)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_silent_holder_is_discounted_by_what_its_tree_wrote_on_the_mount(self) -> None:
        holder = self.holder(self.mount, self.mount, 'head -c "$N" /dev/zero > out', free=100)
        self.assertIn("1 GiB reserved", self.status(self.mount, free=100))
        self.write(holder)
        status = self.status(self.mount, free=99.5)
        self.assertIn("99.5 GiB free, 0.5 GiB reserved", status)
        self.assertIn(f"pid {holder.pid} ", status)
        self.assertIn(f"wrote 0.5 GiB on {self.mount}, 0.5 GiB still counted", status)
        # The discount never exceeds how far free space fell since the reservation.
        self.assertIn("100.0 GiB free, 1 GiB reserved", self.status(self.mount, free=100))
        self.assertIn("99.8 GiB free, 0.8 GiB reserved", self.status(self.mount, free=99.8))
        # --written stays the precise override.
        renewed = self.gate("renew", "auto-lane", "--written", "0.25", "--mount", str(self.mount), free=99.5)
        self.assertEqual(renewed.returncode, 0, renewed.stdout + renewed.stderr)
        self.assertIn("0.25 GiB written, 0.75 GiB still counted", self.status(self.mount, free=99.5))

    def test_measured_holders_share_one_cap(self) -> None:
        """Two reservations measuring the same 0.5 GiB together get no more than free space fell."""
        holder = self.holder(self.mount, self.mount, 'head -c "$N" /dev/zero > out', free=100, names=("one", "two"))
        self.write(holder)
        status = self.status(self.mount, free=99.5)
        self.assertIn("99.5 GiB free, 1.5 GiB reserved", status)
        self.assertEqual(status.count("wrote 0.25 GiB"), 2, status)

    def test_only_the_reserving_agents_commands_count_under_a_shared_harness(self) -> None:
        """omp runs every subagent's commands as children of one process; only the reserver's count."""
        write = (
            "PI_SESSION_ID=lane-b bash -c 'head -c \"$0\" /dev/zero > theirs; touch b-done; exec sleep 120' \"$N\" & "
            "PI_SESSION_ID=lane-a bash -c 'head -c \"$0\" /dev/zero > mine; touch a-done; exec sleep 120' $((N / 2)) & "
            "while [ ! -e a-done ] || [ ! -e b-done ]; do sleep 0.05; done"
        )
        holder = self.holder(self.mount, self.mount, write, free=100, reserve_env="PI_SESSION_ID=lane-a")
        self.write(holder)
        self.assertEqual(self.rows_of(self.mount)["auto-lane"][-1], "lane-a")
        status = self.status(self.mount, free=98.5)
        self.assertIn(f"wrote 0.25 GiB on {self.mount}, 0.75 GiB still counted", status)

    def rows_of(self, mount: Path) -> dict[str, list[str]]:
        ledger = self.state / (str(mount).replace("/", "_") + ".tsv")
        return {line.split("\t")[0]: line.split("\t")[1:] for line in ledger.read_text().splitlines()}

    def test_a_holder_whose_cwd_is_on_another_mount_is_not_discounted(self) -> None:
        """/proc/<pid>/io counts every file system: bytes written on the tmp disk are not /dev's."""
        other = Path("/dev")
        self.assertNotEqual(os.stat(other).st_dev, os.stat(self.mount).st_dev)
        holder = self.holder(other, self.mount, 'head -c "$N" /dev/zero > out', free=100)
        self.write(holder)
        status = self.status(other, free=99)
        self.assertIn("99.0 GiB free, 1 GiB reserved", status)
        self.assertIn(f"pid {holder.pid} ", status)
        self.assertIn("wrote 0 GiB on /dev, 1 GiB still counted", status)

    def test_a_holder_writing_through_a_file_on_another_disk_is_not_discounted(self) -> None:
        """Its cwd is on the mount, but it holds a file open for writing on another disk."""
        elsewhere = other_disk(self.mount)
        if elsewhere is None:
            self.skipTest("no second disk-backed file system on this box")
        target = Path(tempfile.mkdtemp(dir=elsewhere)) / "out"
        self.addCleanup(lambda: (target.unlink(missing_ok=True), target.parent.rmdir()))
        holder = self.holder(self.mount, self.mount, f'exec 3>"{target}"; head -c "$N" /dev/zero >&3', free=100)
        self.write(holder)
        self.assertEqual(target.stat().st_size, self.MIB << 20)
        self.assertIn("99.0 GiB free, 1 GiB reserved", self.status(self.mount, free=99))

    def test_a_holder_whose_process_is_gone_is_charged_in_full(self) -> None:
        holder = self.holder(self.mount, self.mount, 'head -c "$N" /dev/zero > out', free=100, stay=False)
        self.write(holder)
        holder.wait(timeout=30)
        status = self.status(self.mount, free=99)
        self.assertIn("99.0 GiB free, 1 GiB reserved", status)
        self.assertIn(f"pid {holder.pid} ", status)
        self.assertIn("gone and nothing reported, counted in full", status)

    def test_the_floor_still_refuses_with_a_measured_discount(self) -> None:
        # A mount the gate does not know has a 20 GiB floor.
        holder = self.holder(self.mount, self.mount, 'head -c "$N" /dev/zero > out', free=21.5)
        self.write(holder)
        # 21 GiB free, 0.5 GiB of the holder's still counted: 0.5 GiB is available.
        self.assertIn("21.0 GiB free, 0.5 GiB reserved, 20 GiB floor, 0.5 GiB available", self.status(self.mount, free=21))
        refused = self.gate("reserve", "0.6", "next-lane", "--mount", str(self.mount), free=21)
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("would leave 19.9 GiB, under the 20 GiB floor", refused.stdout)
        self.assertIn("wrote 0.5 GiB", refused.stdout)
        granted = self.gate("reserve", "0.4", "next-lane", "--mount", str(self.mount), free=21)
        self.assertEqual(granted.returncode, 0, granted.stdout)


if __name__ == "__main__":
    unittest.main()
