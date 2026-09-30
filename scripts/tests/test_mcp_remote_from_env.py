#!/usr/bin/env python3
"""The home-assistant MCP launch keeps HA_MCP_URL off every command line.

The URL is the credential (its webhook id is the whole secret), and every user can read
`/proc/<pid>/cmdline`, so any `ps` or `pgrep -af` prints what a launch puts in an argv. The launch,
as omp/mcp.json and the skill's `mcp:` frontmatter define it, hands mcp-remote the URL through
scripts/mcp-remote-from-env: mcp-remote still receives it in `process.argv`, the kernel's command
line of every process in the launch never holds it, and the variable is gone from the environment
mcp-remote and its children see. The wrapper refuses an unset or empty variable (exit 2) by name.

Technique: stub `secrets` (injects each named key from STUB_SECRET_<KEY>) and `npx` (puts a fake
mcp-remote package's bin dir first on PATH and runs the command as its child, as `npm exec` does)
first on PATH. The fake package's dist/proxy.js records its argv, `/proc/self/cmdline` and the
cmdline of each ancestor up to the test, its environment and a child's.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
WRAPPER = DOTFILES / "scripts" / "mcp-remote-from-env"
MCP_JSON = DOTFILES / "omp" / "mcp.json"
SKILL = DOTFILES / "plugins" / "sjawhar" / "skills" / "home-assistant" / "SKILL.md"
NODE_DIR = Path(
    subprocess.run(
        ["node", "-e", "process.stdout.write(process.execPath)"], capture_output=True, text=True, check=True
    ).stdout
).parent

SECRETS_STUB = r"""
while [[ "$1" != -- ]]; do
    src="STUB_SECRET_$1"
    export "$1=${!src}"
    unset "$src"
    shift
done
shift
exec "$@"
"""

# `npx -y -p PKG CMD ARGS` runs CMD; `npx -y PKG ARGS` runs PKG's bin. Not exec'd, so this process
# stays in the tree with its own argv, as npm does.
NPX_STUB = r"""
[[ "${1:-}" == -y ]] && shift
if [[ "${1:-}" == -p ]]; then
    shift 2
else
    pkg="${1%@*}"
    set -- "${pkg##*/}" "${@:2}"
fi
PATH="$NPX_BIN:$PATH" "$@" || exit $?
"""

PROXY_STUB = r"""#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const cmdline = (pid) => readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0").filter(Boolean);
const cmdlines = [cmdline("self")];
let pid = process.ppid;
while (pid > 1 && pid !== Number(process.env.STUB_STOP_PID)) {
    cmdlines.push(cmdline(pid));
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    pid = Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]);
}
const childEnv = JSON.parse(
    execFileSync(process.execPath, ["-e", "process.stdout.write(JSON.stringify(process.env))"], { encoding: "utf8" })
);
writeFileSync(process.env.STUB_OUT, JSON.stringify({ argv: process.argv, cmdlines, env: process.env, childEnv }));
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def skill_launch() -> list[str]:
    frontmatter = SKILL.read_text(encoding="utf-8").split("---\n")[1]
    block = re.search(r"^  home-assistant:\n    command: (\S+)\n    args: (\[.*\])$", frontmatter, re.M)
    assert block, f"no home-assistant mcp launch in {SKILL}"
    return [block[1], *json.loads(block[2])]


def mcp_json_launch() -> list[str]:
    server = json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["home-assistant"]
    return [server["command"], *server["args"]]


class HomeAssistantLaunch(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        package = root / "npx" / "node_modules" / "mcp-remote"
        (package / "dist").mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "mcp-remote", "type": "module", "bin": {"mcp-remote": "dist/proxy.js"}}),
            encoding="utf-8",
        )
        proxy = package / "dist" / "proxy.js"
        proxy.write_text(PROXY_STUB, encoding="utf-8")
        proxy.chmod(0o755)
        self.npx_bin = package.parent / ".bin"
        self.npx_bin.mkdir()
        (self.npx_bin / "mcp-remote").symlink_to("../mcp-remote/dist/proxy.js")
        stubs = root / "stubs"
        stubs.mkdir()
        write_stub(stubs, "secrets", SECRETS_STUB)
        write_stub(stubs, "npx", NPX_STUB)
        self.out = root / "proxy.json"
        self.fake_id = f"fake-{uuid.uuid4().hex}"
        self.url = f"http://127.0.0.1:9/api/webhook/{self.fake_id}"
        self.env = {
            # scripts/ is on the PATH of every omp MCP server (.bashrc, agentbox).
            "PATH": f"{stubs}:{DOTFILES / 'scripts'}:{NODE_DIR}:/usr/bin:/bin",
            "HOME": str(root),
            "NPX_BIN": str(self.npx_bin),
            "STUB_OUT": str(self.out),
            "STUB_STOP_PID": str(os.getpid()),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_launch(self, argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, capture_output=True, text=True, env={**self.env, **env}, timeout=30)

    def test_the_url_reaches_mcp_remote_and_no_command_line(self) -> None:
        for source, argv in (("omp/mcp.json", mcp_json_launch()), ("SKILL.md frontmatter", skill_launch())):
            with self.subTest(source=source):
                self.out.unlink(missing_ok=True)
                result = self.run_launch(argv, STUB_SECRET_HA_MCP_URL=self.url)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(self.out.read_text(encoding="utf-8"))
                self.assertEqual(record["argv"][2:], [self.url, "--allow-http"])
                for cmdline in record["cmdlines"]:
                    self.assertNotIn(self.fake_id, " ".join(cmdline))
                for seen in ("env", "childEnv"):
                    self.assertNotIn("HA_MCP_URL", record[seen])
                    self.assertFalse([k for k, v in record[seen].items() if self.fake_id in v], seen)

    def test_an_unset_or_empty_variable_is_refused_by_name(self) -> None:
        path = f"{self.npx_bin}:{self.env['PATH']}"
        for name, env in (("unset", {}), ("empty", {"HA_MCP_URL": ""})):
            with self.subTest(case=name):
                result = self.run_launch([str(WRAPPER), "HA_MCP_URL", "--allow-http"], PATH=path, **env)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("HA_MCP_URL", result.stderr)
                self.assertFalse(self.out.exists(), "mcp-remote must not start")


if __name__ == "__main__":
    unittest.main()
