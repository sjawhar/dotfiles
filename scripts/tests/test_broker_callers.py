#!/usr/bin/env python3
"""The gws tier commands (gws/accounts.json), the mailbox credential commands (mailbox.toml) and the
Retell MCP server (omp/mcp.json) that ask the secrets broker run its client, agent-secrets, after
agent-secrets/broker-url.sh.

The client exits 2 ("AGENT_SECRETS_URL is required") when the process has no broker URL. omp's eval
kernel passes none on (omp/config.yml's eval.envPassthrough lists no AGENT_SECRETS_ name), and on a
machine without the helper scripts/agent-secrets-session starts omp before it loads broker-url.sh, so
an MCP server omp starts gets none either. So each command runs here with no AGENT_SECRETS_*
variable, a fresh HOME and a recording client first on PATH, and the client must get broker.env's
URL, the secret name first, and the command's own reason, never the placeholder "no reason given"
that a deleted wrapper wrote in a caller's place. Two planted commands, one without the loader and
one with the placeholder, show that each check fails on the defect it is for.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
COMMITTED_URL = (DOTFILES / "agent-secrets" / "broker.env").read_text().split("AGENT_SECRETS_URL=", 1)[1].split()[0]
PLACEHOLDER = "no reason given"

# The recording client: one argument per line, then the broker URL it was given. It runs nothing.
CLIENT = r"""#!/bin/sh
for arg do printf '%s\n' "$arg"; done > "$CLIENT_LOG"
printf '%s\n' "${AGENT_SECRETS_URL:-}" > "$CLIENT_LOG.url"
"""


def broker_commands() -> dict[str, list[str]]:
    """Every gws tier command, mailbox credential command and omp MCP server that names agent-secrets."""
    commands: dict[str, list[str]] = {}
    for account, entry in json.loads((DOTFILES / "gws" / "accounts.json").read_text()).items():
        for tier, value in entry.get("tiers", {}).items():
            if "agent-secrets" in " ".join(value["command"]):
                commands[f"gws {account} {tier}"] = list(value["command"])
    mailbox = tomllib.loads((DOTFILES / "mailbox.toml").read_text())
    for account, entry in mailbox.get("accounts", {}).items():
        for key, value in entry.items():
            if key.endswith("_credential_cmd") and "agent-secrets" in " ".join(value):
                commands[f"mailbox {account} {key}"] = list(value)
    for name, server in json.loads((DOTFILES / "omp" / "mcp.json").read_text())["mcpServers"].items():
        argv = [server.get("command", ""), *server.get("args", [])]
        if "agent-secrets" in " ".join(argv):
            commands[f"omp mcp {name}"] = argv
    return commands


def omp_expand(value: str, environment: dict[str, str]) -> str:
    """omp's expansion of ${VAR} and ${VAR:-default} in an MCP server's config before it starts the
    server (oh-my-pi packages/coding-agent/src/discovery/helpers.ts, expandEnvVars)."""

    def one(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = environment.get(name)
        if found is not None and (default is None or found != ""):
            return found
        return default if default is not None else match.group(0)

    return re.sub(r"\$\{([^}:]+)(?::-([^}]*))?\}", one, value)


def problems(command: list[str], dotfiles_dir_set: bool = True) -> list[str]:
    """What the client did not get when command ran with no broker URL in its environment. Without
    DOTFILES_DIR, the dotfiles are found at $HOME/.dotfiles, as on a machine that installed them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "bin").mkdir()
        (root / "home").mkdir()
        client = root / "bin" / "agent-secrets"
        client.write_text(CLIENT)
        client.chmod(0o755)
        log = root / "client.log"
        environment = {"PATH": f"{root / 'bin'}:/usr/bin:/bin", "HOME": str(root / "home"), "CLIENT_LOG": str(log)}
        if dotfiles_dir_set:
            environment["DOTFILES_DIR"] = str(DOTFILES)
        else:
            (root / "home" / ".dotfiles").symlink_to(DOTFILES)
        # gws appends its own path and arguments to a tier command; a command that already
        # names its own command after `--` gets nothing more.
        argv = command + (["gws-stand-in"] if command[-1] == "--" else [])
        result = subprocess.run(argv, env=environment, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not log.exists():
            return [f"exit {result.returncode}, client not run: {result.stderr.strip()}"]
        found = []
        url = Path(f"{log}.url").read_text().strip()
        if url != COMMITTED_URL:
            found.append(f"the client got AGENT_SECRETS_URL {url!r}, not broker.env's {COMMITTED_URL!r}")
        args = log.read_text().splitlines()
        if not args or args[0].startswith("-"):
            found.append(f"no secret name before the flags: {args}")
        reason = args[args.index("--reason") + 1] if "--reason" in args[:-1] else ""
        if not reason.strip() or reason.lower().startswith(PLACEHOLDER):
            found.append(f"the reason is {reason!r}")
        return found


class BrokerCallersTest(unittest.TestCase):
    def test_there_are_commands_to_check(self) -> None:
        # Seven gws tiers, five mailbox commands and the Retell MCP server ask the broker today; an
        # empty set would pass every row below with nothing checked.
        self.assertEqual(len(broker_commands()), 13, sorted(broker_commands()))

    def test_each_command_gives_the_client_the_url_its_name_and_its_own_reason(self) -> None:
        for name, command in broker_commands().items():
            for dotfiles_dir_set in (True, False):
                with self.subTest(command=name, dotfiles_dir_set=dotfiles_dir_set):
                    self.assertEqual(problems(command, dotfiles_dir_set), [])

    def test_the_retell_server_survives_omp_s_expansion_of_its_arguments(self) -> None:
        retell = broker_commands()["omp mcp retell"]
        for environment in ({"DOTFILES_DIR": str(DOTFILES)}, {}):
            with self.subTest(dotfiles_dir_set=bool(environment)):
                expanded = [omp_expand(arg, environment) for arg in retell]
                self.assertEqual(problems(expanded, dotfiles_dir_set=bool(environment)), [])

    def test_a_command_without_the_loader_is_caught(self) -> None:
        planted = ["agent-secrets", "GWS_EXAMPLE_OAUTH", "--reason", "gws example", "--"]
        self.assertTrue(any("AGENT_SECRETS_URL" in problem for problem in problems(planted)))

    def test_a_command_with_the_placeholder_reason_is_caught(self) -> None:
        command = next(iter(broker_commands().values()))
        planted = [PLACEHOLDER + " | session: x" if arg == command[command.index("--reason") + 1] else arg for arg in command]
        self.assertTrue(any("the reason is" in problem for problem in problems(planted)))


if __name__ == "__main__":
    unittest.main()
