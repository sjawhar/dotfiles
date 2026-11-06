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
        # The script's own default is the machine's /run/user/$UID/post-review, shared by every
        # agent box; a test must not write a token there, so each run names a scratch cache.
        self.cache = self.runtime / "post-review"
        self.env = {
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "XDG_RUNTIME_DIR": str(self.runtime),
            "POST_REVIEW_CACHE_DIR": str(self.cache),
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
        lock_path = self.cache / "acme-widgets.lock"
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
        exec(source[start:end], {"TOKEN_SECRET": "K"}, namespace)
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

    def test_the_mint_step_takes_the_owners_token_from_the_brokers_release(self):
        """A cold cache takes the owner's token from the release the broker put in TOKEN_SECRET."""
        namespace: dict = {}
        source = SCRIPT.read_text()
        start = source.index("MINT_SCRIPT = (")
        end = source.index("\n)\n", start) + 3
        exec(source[start:end], {"TOKEN_SECRET": "K"}, namespace)
        cache = Path(self.tmp.name) / "cold.token"
        release = {"installations": {"Acme": {"installation_id": 7, "token": "ghs_minted",
                                              "expires_at": "2099-01-01T00:00:00Z"}}}
        env = {**os.environ, "K": json.dumps(release)}
        run = subprocess.run(
            [sys.executable, "-c", namespace["MINT_SCRIPT"], "acme", "widgets", str(cache), str(cache) + ".lock"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["token"], "ghs_minted")
        self.assertEqual(json.loads(cache.read_text())["token"], "ghs_minted")
        self.assertEqual(oct(cache.stat().st_mode & 0o777), "0o600")

    def test_the_request_names_the_token_secret_narrowed_to_the_repository(self):
        """secret-run is asked for LEGION_REVIEW_APP narrowed to owner and repository, never a key."""
        source = SCRIPT.read_text()
        self.assertIn('TOKEN_SECRET = "LEGION_REVIEW_APP"', source)
        self.assertNotIn("PRIVATE_KEY", source)
        self.assertIn('"--github-owner",\n            owner,\n            "--github-repo",\n            repo,', source)

# cache_dir() asked in a child process, so each case gets its own environment.
WHERE = f"""
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader("post_review", {str(SCRIPT)!r})
spec = importlib.util.spec_from_loader("post_review", loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
print(module.cache_path("acme", "widgets"))
"""


class CacheDirectory(unittest.TestCase):
    """The token cache is the machine's, not one box's.

    An agent box has a private /run/user/$UID, so while the cache followed $XDG_RUNTIME_DIR each
    box asked Sami for its own grant of the human-tier review secret, and a box recreated by
    `agentbox move` threw away the hour its session already had.
    """

    def where(self, **env: str) -> Path:
        run = subprocess.run([sys.executable, "-c", WHERE], capture_output=True, text=True,
                             env={**os.environ, **env}, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return Path(run.stdout.strip())

    def test_a_private_runtime_directory_does_not_move_the_cache(self):
        with tempfile.TemporaryDirectory() as box:
            got = self.where(XDG_RUNTIME_DIR=box, POST_REVIEW_CACHE_DIR="")
        self.assertEqual(got.parent, Path(f"/run/user/{os.getuid()}/post-review"))

    def test_a_context_without_the_shared_directory_still_has_a_cache(self):
        """The fallback: $XDG_RUNTIME_DIR, so a box created before agentbox bound the shared
        directory in, or a pod with no /run/user/$UID, still reuses its own hour."""
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / "shared"
            shared.mkdir(mode=0o500)
            runtime = Path(root) / "run"
            runtime.mkdir()
            try:
                got = self.where(XDG_RUNTIME_DIR=str(runtime),
                                 POST_REVIEW_CACHE_DIR=str(shared / "post-review"))
                self.assertEqual(got.parent, runtime / "post-review")
            finally:
                shared.chmod(0o700)


# A broker stand-in whose approval never comes: it honours the `--wait` it was given, prints the
# two lines agent-secrets prints around that wait, and exits 75, leaving the request pending.
PENDING_STUB = """#!/bin/bash
echo x >> "$STUB_CALLS"
wait=30m
while (( $# )); do
  if [[ "$1" == --wait ]]; then wait="$2"; shift 2; else shift; fi
done
echo "agent-secrets: request req-abc123 to the broker is waiting for approval; waiting up to $wait" >&2
sleep "${wait%s}"
echo "agent-secrets: request req-abc123 is still waiting for approval; nothing was run." >&2
exit 75
"""


class PendingGrant(unittest.TestCase):
    """A grant nobody has approved yet gives the caller its command back, bounded.

    On 2026-11-06 four reviewers each sat 37 to 53 minutes in the broker's 30-minute default
    wait, and their blocked children helped wedge a session's shell (dispatch://INFRA-515). No
    path through installation_token may now outlast POST_REVIEW_GRANT_WAIT.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.calls = root / "calls"
        self.calls.write_text("")
        self.runtime = root / "run"
        self.runtime.mkdir()
        self.cache = self.runtime / "post-review"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "XDG_RUNTIME_DIR": str(self.runtime),
            "POST_REVIEW_CACHE_DIR": str(self.cache),
            "STUB_CALLS": str(self.calls),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install(self, stub: str) -> None:
        path = self.bin / "secret-run"
        path.write_text(stub)
        path.chmod(0o755)

    def caller(self, grant_wait: int, ask_wait: int = 120, stub_wait: float = 0) -> subprocess.Popen:
        env = {**self.env, "STUB_WAIT": str(stub_wait),
               "POST_REVIEW_ASK_WAIT_SECONDS": str(ask_wait),
               "POST_REVIEW_GRANT_WAIT": str(grant_wait)}
        return subprocess.Popen([sys.executable, "-c", CALLER], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def broker_calls(self) -> int:
        return len(self.calls.read_text().splitlines())

    def test_a_pending_grant_exits_tempfail_within_the_bound_and_names_the_request(self):
        self.install(PENDING_STUB)
        started = time.monotonic()
        caller = self.caller(grant_wait=3)
        out, err = caller.communicate(timeout=60)
        took = time.monotonic() - started
        self.assertEqual(caller.returncode, 75, err)
        self.assertEqual(out.strip(), "")
        self.assertIn(
            "post-review: the LEGION_REVIEW_APP grant for acme/widgets is waiting for approval"
            " (request req-abc123); nothing was posted; run the same command again after it is"
            " approved",
            err,
        )
        # What is left of the bound reached the broker as its own --wait, whole seconds of it, so
        # nothing waited the 30-minute default.
        self.assertRegex(err, r"waiting up to [1-3]s")
        self.assertLess(took, 20)

    def test_a_granted_request_posts_as_before(self):
        self.install(STUB)
        caller = self.caller(grant_wait=90, stub_wait=1)
        out, err = caller.communicate(timeout=60)
        self.assertEqual(caller.returncode, 0, err)
        self.assertTrue(out.strip().startswith("ghs_"), out)
        self.assertEqual(self.broker_calls(), 1)

    def test_the_wait_on_another_sessions_request_is_bounded_by_the_grant_wait(self):
        """A caller whose whole budget goes on another session's `.asking` exits 75, asking nobody."""
        self.install(PENDING_STUB)
        stuck = self.caller(grant_wait=60)
        time.sleep(1)
        started = time.monotonic()
        second = self.caller(grant_wait=3, ask_wait=60)
        out, err = second.communicate(timeout=60)
        took = time.monotonic() - started
        stuck.kill()
        stuck.communicate()
        self.assertEqual(second.returncode, 75, err)
        self.assertEqual(out.strip(), "")
        self.assertIn("is waiting for approval; nothing was posted", err)
        self.assertNotIn("asking as well", err)
        self.assertLess(took, 20)
        self.assertEqual(self.broker_calls(), 1)



if __name__ == "__main__":
    unittest.main()
