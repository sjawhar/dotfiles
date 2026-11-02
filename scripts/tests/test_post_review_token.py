#!/usr/bin/python3
"""post-review's token mint: the broker's wait for a human holds no lock another post needs.

On 2026-11-02 at 19:31Z one post-review waiting for an approval of the review key held the
per-repo lock for more than nine minutes and stopped every other session's post. These tests run
the script's installation_token with a stub `secret-run` on PATH that stands for the broker: it
counts its calls, waits (the approval), and then runs the mint step or writes the cache itself.

Technique: the script is loaded as a module from its path (it has no .py name), each caller runs
in its own process, and XDG_RUNTIME_DIR points at a scratch directory, so no real cache, broker
or GitHub call is touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "post-review"

CALLER = f"""
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("post_review", {str(SCRIPT)!r})
spec = importlib.util.spec_from_loader("post_review", loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
print(module.installation_token("acme", "widgets"))
"""

# The broker stand-in: count the call, wait as a human would, then write a token to the cache
# path the mint step was handed (argv after `-c <script> owner repo`) and print it.
STUB = """#!/bin/bash
echo x >> "$STUB_CALLS"
sleep "$STUB_WAIT"
while [[ "$1" != -c ]]; do shift; done
cache="$5"
printf '{"token": "ghs_%s", "expires_at": %s}' "$$" "$(( $(date +%s) + 3600 ))" > "$cache"
cat "$cache"
"""


class TokenLocking(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "bin").mkdir()
        stub = root / "bin" / "secret-run"
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.calls = root / "calls"
        self.calls.write_text("")
        self.runtime = root / "run"
        self.runtime.mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "XDG_RUNTIME_DIR": str(self.runtime),
            "STUB_CALLS": str(self.calls),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def caller(self, wait: float, ask_wait: int = 120) -> subprocess.Popen:
        env = {**self.env, "STUB_WAIT": str(wait), "POST_REVIEW_ASK_WAIT_SECONDS": str(ask_wait)}
        return subprocess.Popen([sys.executable, "-c", CALLER], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def broker_calls(self) -> int:
        return len(self.calls.read_text().splitlines())

    def test_a_second_caller_takes_the_first_callers_token_and_asks_nobody(self):
        first = self.caller(wait=3)
        time.sleep(1)
        second = self.caller(wait=3)
        out1, _ = first.communicate(timeout=60)
        out2, _ = second.communicate(timeout=60)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(out1.strip(), out2.strip())
        self.assertEqual(self.broker_calls(), 1)

    def test_a_request_nobody_approves_holds_another_post_only_for_the_ask_wait(self):
        stuck = self.caller(wait=60)
        time.sleep(1)
        started = time.monotonic()
        second = self.caller(wait=0, ask_wait=2)
        out, err = second.communicate(timeout=30)
        took = time.monotonic() - started
        stuck.kill()
        stuck.communicate()
        self.assertEqual(second.returncode, 0, err)
        self.assertTrue(out.strip().startswith("ghs_"))
        self.assertIn("asking as well", err)
        self.assertLess(took, 15)
        self.assertEqual(self.broker_calls(), 2)

    def test_no_lock_is_held_across_the_broker_wait(self):
        """The cache lock is free while a request waits for its approval."""
        import fcntl

        stuck = self.caller(wait=60)
        time.sleep(2)
        lock_path = self.runtime / "post-review" / "acme-widgets.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stuck.kill()
        stuck.communicate()

    def test_the_mint_step_reuses_a_token_another_grant_wrote_under_the_lock(self):
        """Two grants that land together mint one token: the second re-reads the cache."""
        namespace: dict = {}
        source = SCRIPT.read_text()
        start = source.index("MINT_SCRIPT = (")
        end = source.index("\n)\n", start) + 3
        exec(source[start:end], {"KEY_SECRET": "K", "APP_ID": "1"}, namespace)
        cache = Path(self.tmp.name) / "cache.token"
        held = {"token": "ghs_live", "expires_at": time.time() + 3600}
        cache.write_text(json.dumps(held))
        env = {k: v for k, v in os.environ.items() if k != "K"}
        run = subprocess.run(
            [sys.executable, "-c", namespace["MINT_SCRIPT"], "acme", "widgets", str(cache), str(cache) + ".lock"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["token"], "ghs_live")


if __name__ == "__main__":
    unittest.main()
