#!/usr/bin/env python3
"""Regression tests for disk-gate's accounting of reserved bytes.

`df` already shows what a holder has written, so a reservation counted at its full size
for its whole life subtracts those bytes twice. The gate counts only the unwritten part of
a reservation (the holder reports what it wrote with `--written`), lets a holder renew
downward at any time, keeps a holder's reservation when an upward renewal is refused, and
still counts a silent holder at its full size. The numbers are LANE-C's refusal on
/mnt/nvme at 2026-11-02T16:21Z: 156.8 GiB free, holders of 25+40+4+2+1 GiB, 78 GiB floor.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "disk-gate"

FAKE_DF = """#!/usr/bin/env bash
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

    def gate(self, *args: str, free: float = FREE_1621) -> subprocess.CompletedProcess:
        env = dict(self.env, FAKE_FREE_GIB=str(free))
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
        self.assertEqual(self.rows()["o16-gt2f91-build"][::2], ["40", "30"])

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
        self.assertEqual(len(self.rows()["lane"]), 2, "a lane that reports nothing keeps the old row shape")
        self.assertIn("7 GiB reserved", self.status_line())
        released = self.gate("release", "lane")
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(self.gate("release", "lane").returncode, 1)
        self.assertEqual(self.gate("reserve", "x", "lane").returncode, 2)
        self.assertEqual(self.gate("reserve", "7", "lane", "--written", "lots").returncode, 2)


if __name__ == "__main__":
    unittest.main()
