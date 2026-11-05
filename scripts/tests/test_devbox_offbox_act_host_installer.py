"""The host installer writes the listener's own key file with a forced command and a node pin.

Why the pin is parsed rather than pattern-matched: the first version of this gate compared the
value against one spelling of one range, and on 2026-11-05 the INFRA lead's own run accepted
`fd7a:115c:a1e0::/48` (the same tailnet in the other family), `100.64.0.0/9`, `100.64.0.0/8`,
`0.0.0.0/0`, `*` and `TODO`, printing each as "only <value> may use the key at all" (the gate of
agent-infra#2194). A `from=` that admits every node on the tailnet is not a restriction, and a
printed line that claims it is, is worse than no line. So every element is parsed, in both
families, and must be ONE address -- which is what the job's node has, its tailnet node being
persistent rather than one device per pass.

Loopback joined the refusals with the 2222 listener: the connection arrives through Tailscale's
proxy, so a loopback source is what the transport looks like on the receiver, not what the caller
is, and a pin to it would admit anything arriving by that route.

Range, loopback, missing-pin and malformed-key cases must fail before installation.
These tests run outside the live dotfiles checkout. They do not prove SSH transport reachability.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent.parent / "installers" / "devbox-offbox-act-host.sh"

NODE_V4 = "100.112.105.99"
NODE_V6 = "fd7a:115c:a1e0::a136:6940"


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """The installer, told this machine is the one it installs on."""
    env = dict(os.environ)
    env["DEVBOX_OFFBOX_HOST"] = subprocess.run(["hostname", "-s"], capture_output=True,
                                               text=True, check=True).stdout.strip()
    env["DEVBOX_OFFBOX_ACT_DIR"] = str(tmp_path / "offbox-act")
    return subprocess.run(["bash", str(INSTALLER), *args], env=env, capture_output=True,
                          text=True, timeout=120)


@pytest.mark.parametrize("value, because", [
    ("100.64.0.0/10", "the tailnet's own IPv4 range"),
    ("100.64.0.0/9", "a supernet of it"),
    ("100.64.0.0/8", "a larger supernet"),
    ("fd7a:115c:a1e0::/48", "the tailnet in IPv6, the range the first gate's one spelling missed"),
    ("fd7a:115c:a1e0::/32", "a supernet of that"),
    ("0.0.0.0/0", "every address there is"),
    ("::/0", "the same in IPv6"),
    ("100.112.105.0/24", "a range of one node's neighbours"),
    ("100.112.105.98/31", "a range of two"),
    ("127.0.0.1", "a proxy's loopback source is not a node identity"),
    ("::1", "IPv6 loopback"),
    ("0.0.0.0", "an unspecified source"),
    ("192.0.2.1", "not a tailnet address"),
    ("*", "sshd's wildcard"),
    ("TODO", "a placeholder without angle brackets"),
    ("<the job's tailnet IPs>", "the placeholder this script used to print"),
    ("sami-devbox", "a host name, which is not an address"),
    ("100.112.105.*", "a pattern"),
    (f"{NODE_V4},100.64.0.0/10", "one good element next to the whole tailnet"),
    (" ", "nothing at all"),
])
def test_a_from_that_is_not_one_node_is_refused(tmp_path: Path, value: str, because: str) -> None:
    done = run(tmp_path, "--from", value)
    assert done.returncode != 0, f"--from {value!r} ({because}) was accepted:\n{done.stdout}"
    assert "restrict,command=" not in done.stdout, "it printed a key line anyway"
    assert "--from takes the job's own node addresses" in done.stderr, done.stderr


@pytest.mark.parametrize("value, printed", [
    (NODE_V4, NODE_V4),
    (f"{NODE_V4}/32", f"{NODE_V4}/32"),
    (NODE_V6, NODE_V6),
    (f"{NODE_V6}/128", f"{NODE_V6}/128"),
    (f"{NODE_V4},{NODE_V6}", f"{NODE_V4},{NODE_V6}"),
    # sshd's from= is comma-separated, so a space-separated pair is normalised rather than printed
    # as one malformed pattern.
    (f"{NODE_V4} {NODE_V6}", f"{NODE_V4},{NODE_V6}"),
    (f"{NODE_V4}, {NODE_V6}", f"{NODE_V4},{NODE_V6}"),
])
def test_one_node_s_addresses_are_accepted_and_normalised(tmp_path: Path, value: str,
                                                          printed: str) -> None:
    done = run(tmp_path, "--from", value)
    assert done.returncode == 0, done.stderr
    line = [l for l in done.stdout.splitlines() if l.startswith("restrict,command=")]
    assert len(line) == 1, done.stdout
    assert f',from="{printed}" ssh-ed25519' in line[0], line[0]
    assert f"only {printed} may use the key at all" in done.stdout


def test_no_from_says_what_is_actually_left(tmp_path: Path) -> None:
    """Print-only --no-from explains that the owner's devices also have network access.
    It cannot install a key into the isolated listener's authorized_keys file."""
    done = run(tmp_path, "--no-from")
    assert done.returncode == 0, done.stderr
    line = [l for l in done.stdout.splitlines() if l.startswith("restrict,command=")]
    assert len(line) == 1 and "from=" not in line[0], done.stdout
    assert "nothing restricts WHERE the key may be used from" in done.stdout
    assert "autogroup:member -> autogroup:self" in done.stdout
    assert "is the only TAG's route" in done.stdout


def test_it_installs_the_pinned_copy_and_can_be_run_again(tmp_path: Path) -> None:
    """An installer that fails the second time is an installer nobody can update with. The record
    it writes is read-only on purpose, so it is replaced through a temporary name."""
    first = run(tmp_path, "--from", NODE_V4)
    assert first.returncode == 0, first.stderr
    installed = tmp_path / "offbox-act"
    for name in ("devbox-offbox-act", "devbox-offbox-probe"):
        assert (installed / name).exists(), f"{name} was not installed"
        assert os.access(installed / name, os.X_OK)
    record = (installed / "PINNED").read_text()
    assert "commit=" in record and "source=" in record
    again = run(tmp_path, "--from", NODE_V4)
    assert again.returncode == 0, again.stderr
    assert not list(installed.glob("*.new")), "a temporary file was left behind"


def test_it_refuses_to_install_on_a_machine_that_is_not_the_watched_one(tmp_path: Path) -> None:
    """The pinned acting half belongs ON the watched machine; the watch runs elsewhere."""
    env = dict(os.environ)
    env["DEVBOX_OFFBOX_HOST"] = "some-other-box"
    env["DEVBOX_OFFBOX_ACT_DIR"] = str(tmp_path / "offbox-act")
    done = subprocess.run(["bash", str(INSTALLER), "--from", NODE_V4], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 1
    assert "not some-other-box" in done.stderr
    assert not (tmp_path / "offbox-act").exists()


def test_public_key_is_installed_with_all_restrictions(tmp_path: Path) -> None:
    key = tmp_path / "throwaway"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    done = run(tmp_path, "--from", NODE_V4, "--public-key", str(key) + ".pub")
    assert done.returncode == 0, done.stderr
    installed = tmp_path / "offbox-act" / "authorized_keys"
    public = Path(str(key) + ".pub").read_text().split()
    assert installed.read_text() == (
        f'restrict,command="{tmp_path}/offbox-act/devbox-offbox-act --forced",'
        f'from="{NODE_V4}" {public[0]} {public[1]} devbox-offbox-watch@dispatch-cluster\n'
    )
    assert installed.stat().st_mode & 0o777 == 0o600
    again = run(tmp_path, "--from", NODE_V4, "--public-key", str(key) + ".pub")
    assert again.returncode == 0, again.stderr
    assert len(installed.read_text().splitlines()) == 1


def test_installing_a_key_requires_from(tmp_path: Path) -> None:
    done = run(tmp_path, "--no-from", "--public-key", str(tmp_path / "unused.pub"))
    assert done.returncode == 2
    assert "--public-key requires --from" in done.stderr
    assert not (tmp_path / "offbox-act").exists()


def test_malformed_public_key_is_not_installed(tmp_path: Path) -> None:
    public = tmp_path / "bad.pub"
    public.write_text("ssh-ed25519 not-a-public-key\n")
    done = run(tmp_path, "--from", NODE_V4, "--public-key", str(public))
    assert done.returncode != 0
    assert not (tmp_path / "offbox-act").exists()
