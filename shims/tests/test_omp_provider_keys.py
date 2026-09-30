#!/usr/bin/env python3
"""Which provider keys reach the omp process shims/omp starts (AGENTC-1085).

The default profile routes every provider through the gateway, so its session must hold no
ANTHROPIC_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY: a key in omp's environment is in every tool
subprocess it spawns, and every command that prints the environment writes it into the
transcript. A named profile gets its own <KEY>_<PROFILE> secrets as the provider keys, and keeps
anything the caller exported.

Technique: the real shims/omp and scripts/agent-secrets-session, with stubs for what they exec
last: `mise` (resolves the command on PATH with the release's tool dir first, as `mise x` does),
`secrets`, `agent-secrets`, and the release omp, which prints the provider keys it was given.
The canary values are fake; a value appearing in the shim's own output would be a leak.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"
# Every variable omp reads for a gateway-routed provider: anthropic takes ANTHROPIC_OAUTH_TOKEN
# before ANTHROPIC_API_KEY (omp's catalog auth/anthropic.kdl, registry/hooks/env.ts).
KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY")
CANARY = "canary-not-a-key-"

# `mise x` resolves its own command in the tool dirs it adds. What its CHILD's PATH holds
# depends on the PATH it is handed (measured, mise 2026.8.10): when that PATH carries mise's own
# shims dir (~/.mise/shims — every PATH .bashrc builds does), the entries ahead of it, the
# dotfiles shims dir among them, stay ahead of the tool dirs, so `command -v omp` in the child
# finds the shim; without it, the tool dirs come first. This stub models the first case, the
# normal one, which is why the guard scans install dirs instead of looking omp up.
MISE_X = r"""
[[ "$1" == x && "$3" == -- ]] || { echo "unexpected mise $*" >&2; exit 99; }
tool="$MISE_DATA_DIR/installs/github-sjawhar-oh-my-pi/1.0.0/bin"
${STUB_MISE_ENV:+export "$STUB_MISE_ENV"}
program="$(PATH="$tool:$PATH" command -v "$4")"
PATH="$PATH:$tool" exec "$program" "${@:5}"
"""

# The stubs stand in for binaries an inherited SHELLOPTS=xtrace does not trace. A stub that
# handles a value hides only those lines from xtrace and turns it back on before it execs, so
# SHELLOPTS reaches the next hop unchanged, as it would through the real binary.
#
# The release omp: one line per provider key it holds.
RELEASE_OMP = "{ set +x; } 2>/dev/null\n" + f"for k in {' '.join(KEYS)}; do\n" + r"""
    [[ -n "${!k+x}" ]] && echo "$k=${!k}"
done
echo "OMP $*"
"""

# `secrets list` names the profile's scoped keys; `secrets NAME… -- cmd` runs cmd with each set.
SECRETS = r"""
if [[ "$1" == list ]]; then
    printf '%s agent\n' $STUB_SECRETS_LIST
    exit 0
fi
names=()
while [[ "$1" != -- ]]; do names+=("$1"); shift; done
shift
traced=""; [[ $- == *x* ]] && traced=1
{ set +x; } 2>/dev/null
for n in "${names[@]}"; do export "$n=scoped-$n"; done
[[ -z "$traced" ]] || set -x
exec "$@"
"""

# A host with the helper: login-status says no credential, `register --exec` runs the command,
# first setting whatever STUB_REGISTER_ENV names (a hop after the shim that adds a variable).
AGENT_SECRETS = r"""
case "$1" in
    launcher) echo none ;;
    register)
        while [[ "$1" != -- ]]; do shift; done
        shift
        ${STUB_REGISTER_ENV:+export "$STUB_REGISTER_ENV"}
        exec "$@"
        ;;
    *) echo "unexpected agent-secrets $*" >&2; exit 99 ;;
esac
"""


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class OmpProviderKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.stub_dir = self.root / "bin"
        write_stub(self.stub_dir, "mise", MISE_X)
        write_stub(self.stub_dir, "secrets", SECRETS)
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS)
        self.mise_data = self.root / "mise"
        write_stub(
            self.mise_data / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin",
            "omp",
            RELEASE_OMP,
        )
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "MISE_DATA_DIR": str(self.mise_data),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            # A seed file already named: the shim never reaches for the real one.
            "NATS_NKEY_SEED_FILE": "/dev/null",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIM), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=60,
        )

    def assert_no_canary(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotIn(CANARY, result.stdout + result.stderr)

    def test_default_profile_session_gets_no_inherited_provider_key(self) -> None:
        """A launch from a shell or session that holds the keys starts omp without them."""
        inherited = {k: f"{CANARY}{k}" for k in KEYS}
        for name, args, extra in (
            ("no profile", ("--version",), {}),
            ("--profile default", ("--profile", "default", "--version"), {}),
            ("OMP_PROFILE empty", ("--version",), {"OMP_PROFILE": ""}),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, **inherited, **extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"OMP {' '.join(args)}\n")
                for k in KEYS:
                    self.assertIn(k, result.stderr)
                self.assert_no_canary(result)

    def test_a_launch_that_dropped_nothing_says_nothing(self) -> None:
        """The notice lands in the transcript of whatever ran omp; it is for a real drop only."""
        result = self.run_shim("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, result.stderr), ("OMP --version\n", ""))

    def test_a_hop_after_the_shim_that_adds_a_key_stops_the_session(self) -> None:
        """A wrapper between the shim and omp that puts a key back fails the launch, loudly."""
        unit = self.home / ".config" / "systemd" / "user" / "agent-secrets-helper.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("", encoding="utf-8")
        for hop, stub_var, key in (
            ("mise x (a project's mise.toml [env])", "STUB_MISE_ENV", "OPENAI_API_KEY"),
            ("agent-secrets register --exec", "STUB_REGISTER_ENV", "GEMINI_API_KEY"),
        ):
            with self.subTest(hop):
                result = self.run_shim("--version", **{stub_var: f"{key}={CANARY}{key}"})
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(key, result.stderr)
                self.assert_no_canary(result)

    def test_an_inherited_xtrace_prints_no_key_value(self) -> None:
        """SHELLOPTS=xtrace in the caller's environment switches tracing on in every bash on the
        chain; whatever the shim or the guard traces goes to stderr, and so into the transcript
        of whatever ran omp. No trace may carry a value."""
        trace = {"SHELLOPTS": "xtrace"}
        for name, args, extra, value in (
            ("default profile, inherited keys", ("--version",), {k: f"{CANARY}{k}" for k in KEYS}, CANARY),
            (
                "named profile, the scoped key renamed",
                ("--profile", "theorem", "--version"),
                {"STUB_SECRETS_LIST": "OPENAI_API_KEY_THEOREM"},
                "scoped-OPENAI_API_KEY_THEOREM",
            ),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, **trace, **extra)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn("+ ", result.stderr)  # tracing was on
                self.assertNotIn(value, result.stderr)
        (self.home / ".env").write_text(f"OPENAI_API_KEY={CANARY}file\n", encoding="utf-8")
        with self.subTest("a dotenv file the guard reads"):
            result = self.run_shim("--version", **trace)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(CANARY, result.stderr)

    def test_a_dotenv_file_omp_loads_for_itself_stops_the_session(self) -> None:
        """omp 18.4.3 loads these into its own environment and every tool shell after the shim's
        last exec, so no environment check sees them. None exists on the devbox, so each one is
        made here, in the scratch HOME, one at a time."""
        agent_override = self.root / "agent-override"
        for name, path, extra in (
            ("~/.env", self.home / ".env", {}),
            ("the config root's .env", self.home / ".omp" / ".env", {}),
            ("the default agent dir's .env", self.home / ".omp" / "agent" / ".env", {}),
            ("the config root under PI_CONFIG_DIR", self.home / ".alt" / ".env", {"PI_CONFIG_DIR": ".alt"}),
            (
                "an agent dir named by PI_CODING_AGENT_DIR",
                agent_override / ".env",
                {"PI_CODING_AGENT_DIR": str(agent_override)},
            ),
        ):
            with self.subTest(name):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'export OPENAI_API_KEY="{CANARY}file"\n', encoding="utf-8")
                try:
                    result = self.run_shim("--version", **extra)
                finally:
                    path.unlink()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"OPENAI_API_KEY in {path}", result.stderr)
                self.assert_no_canary(result)
        # omp parses with Bun's node:util parseEnv, which also takes `NAME: value` and backticks.
        for spelling in (f"GEMINI_API_KEY: {CANARY}colon\n", f"GEMINI_API_KEY=`{CANARY}bt`\n"):
            with self.subTest(spelling.split(CANARY)[0]):
                (self.home / ".env").write_text(spelling, encoding="utf-8")
                try:
                    result = self.run_shim("--version")
                finally:
                    (self.home / ".env").unlink()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("GEMINI_API_KEY in", result.stderr)
                self.assert_no_canary(result)

    def test_a_dotenv_file_that_assigns_no_provider_key_value_lets_the_session_start(self) -> None:
        """The other direction: other names, empty values and comments are not a key."""
        body = (
            "OTHER_API_KEY=x\n"
            "MY_OPENAI_API_KEY=x\n"
            "OPENAI_API_KEY=\n"
            'GEMINI_API_KEY=""\n'
            "ANTHROPIC_API_KEY='' # left empty\n"
            "# OPENAI_API_KEY=commented-out\n"
            "OPENAI_API_KEY: ``\n"
        )
        for path in (self.home / ".env", self.home / ".omp" / ".env", self.home / ".omp" / "agent" / ".env"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        result = self.run_shim("--version")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "OMP --version\n", ""))

    def test_named_profile_gets_its_scoped_key_and_keeps_the_callers(self) -> None:
        """Negative control: a named profile's keys are its own <KEY>_<PROFILE> secrets."""
        result = self.run_shim(
            "--profile",
            "theorem",
            "--version",
            STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM",
            ANTHROPIC_API_KEY="exported-by-the-caller",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ANTHROPIC_API_KEY=exported-by-the-caller\n"
            "OPENAI_API_KEY=scoped-OPENAI_API_KEY_THEOREM\n"
            "OMP --profile theorem --version\n",
        )

    def test_the_keys_follow_the_profile_omp_runs(self) -> None:
        """omp 18.4.3 takes a --profile flag over OMP_PROFILE, and PI_PROFILE when OMP_PROFILE is
        unset; a shim that decided otherwise would bill one profile's keys to another, or hand
        keys to a default-profile session."""
        scoped = "OPENAI_API_KEY=scoped-OPENAI_API_KEY_THEOREM\n"
        for name, args, extra, keys in (
            (
                "--profile default under an inherited OMP_PROFILE=theorem",
                ("--profile", "default", "--version"),
                {"OMP_PROFILE": "theorem", "OPENAI_API_KEY": f"{CANARY}inherited"},
                "",
            ),
            (
                "--profile theorem under an empty OMP_PROFILE",
                ("--profile", "theorem"),
                {"OMP_PROFILE": ""},
                scoped,
            ),
            ("PI_PROFILE=theorem with no OMP_PROFILE", ("--version",), {"PI_PROFILE": "theorem"}, scoped),
            (
                "an empty OMP_PROFILE outranks PI_PROFILE, as resolveProfileEnv does",
                ("--version",),
                {"OMP_PROFILE": "", "PI_PROFILE": "theorem", "OPENAI_API_KEY": f"{CANARY}inherited"},
                "",
            ),
            (
                "the last --profile wins, and names the default profile",
                ("--profile", "theorem", "--profile", "default"),
                {"OPENAI_API_KEY": f"{CANARY}inherited"},
                "",
            ),
            ("the last --profile wins, and names a profile", ("--profile", "default", "--profile", "theorem"), {}, scoped),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM", **extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{keys}OMP {' '.join(args)}\n")
                self.assert_no_canary(result)

    def test_a_shape_omp_reads_no_profile_from_is_a_default_profile_session(self) -> None:
        """omp stops reading global flags at `--` and at a first-token subcommand, and gives a
        string flag's value to that flag. The shim read the --profile in all of these, so the
        session omp actually ran as the default profile started with the caller's keys — or, with
        --system-prompt, with another profile's scoped key."""
        inherited = {k: f"{CANARY}{k}" for k in KEYS}
        for name, args in (
            ("bench, omp's own documented subcommand example", ("bench", "opus", "--profile", "theorem")),
            ("grep", ("grep", "--profile", "theorem")),
            ("shell", ("shell", "--profile", "theorem")),
            ("a string flag takes the next token whatever it looks like", ("--system-prompt", "--profile", "theorem", "hi")),
            ("after the end of options", ("--", "--profile", "theorem")),
            ("after the end of options, past a message", ("--", "hi", "--profile", "theorem")),
            (
                "a subcommand after a --profile pair omp strips is still the first token",
                ("--profile", "default", "bench", "--profile", "theorem"),
            ),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM", **inherited)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"OMP {' '.join(args)}\n")
                for k in KEYS:
                    self.assertIn(k, result.stderr)
                self.assert_no_canary(result)

    def test_a_shape_omp_does_read_the_profile_from_still_gets_its_scoped_key(self) -> None:
        """The other side of the same rules: a value-less flag before --profile, a launch-shaped
        command, and a string flag that already took its value all leave the profile readable."""
        scoped = "OPENAI_API_KEY=scoped-OPENAI_API_KEY_THEOREM\n"
        for name, args in (
            ("a value-less flag first", ("--print", "--profile", "theorem")),
            ("acp is launch-shaped, not a dispatched subcommand", ("acp", "--profile", "theorem")),
            ("a string flag that already has its value", ("--model", "opus", "--profile", "theorem")),
            ("the --profile= spelling", ("--profile=theorem", "--version")),
            (
                "a readable --profile after one a string flag took",
                ("--system-prompt", "--profile", "a", "--profile", "theorem"),
            ),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{scoped}OMP {' '.join(args)}\n")

    def test_a_profile_the_shim_cannot_read_is_the_default_even_under_an_env_profile(self) -> None:
        """Every tool shell of a named-profile session carries OMP_PROFILE, and theorem's scoped key
        under its provider name. omp 18.4.3 reads `--profile default` in each of these (measured,
        or read from its extractProfileFlags at the release tag), so a shim that let the env's
        profile stand would start a default-profile omp holding theorem's key."""
        inherited = {k: f"{CANARY}{k}" for k in KEYS}
        for name, args in (
            ("a message word first", ("hello", "--profile", "default")),
            ("an extension flag first", ("--some-ext-flag", "--profile", "default")),
            ("a short-flag cluster first", ("-pc", "--profile", "default")),
            ("a --name=value flag first", ("--model=opus", "--profile", "default", "-p", "hi")),
            ("another --name=value flag", ("--thinking=high", "--profile", "default")),
            ("the last of two, the first spelled with =", ("--profile=theorem", "--profile", "default")),
            (
                "a subcommand after a stripped --profile pair",
                ("--profile", "default", "grep", "--profile", "theorem"),
            ),
            ("a string flag that took `--` as its value", ("--model", "--", "--profile=default")),
        ):
            with self.subTest(name):
                result = self.run_shim(
                    *args, OMP_PROFILE="theorem", STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM", **inherited
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"OMP {' '.join(args)}\n")
                self.assert_no_canary(result)

    def test_a_subcommand_under_an_env_profile_still_gets_that_profiles_key(self) -> None:
        """omp runs a subcommand under the environment's profile, so with no --profile in sight
        the shim must not withhold the key."""
        result = self.run_shim("shell", OMP_PROFILE="theorem", STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OPENAI_API_KEY=scoped-OPENAI_API_KEY_THEOREM\nOMP shell\n")

    def test_a_profile_the_shim_leaves_unread_is_named_at_launch(self) -> None:
        """omp runs theorem for these, but the shim cannot be sure, so the session starts with no
        key: a user who typed it must hear that, and the working form, at the terminal rather
        than meet a missing-key error from the provider."""
        for name, args in (
            ("a message first", ("fix the bug", "--profile", "theorem")),
            ("an unlisted option first", ("--some-ext-flag", "--profile=theorem")),
        ):
            with self.subTest(name):
                result = self.run_shim(*args, STUB_SECRETS_LIST="OPENAI_API_KEY_THEOREM")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"OMP {' '.join(args)}\n")
                self.assertIn("OMP_PROFILE=NAME omp", result.stderr)
        # An unread `--profile default` asked for what it gets: no notice.
        result = self.run_shim("--some-ext-flag", "--profile", "default", OMP_PROFILE="theorem")
        self.assertEqual((result.returncode, result.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
