"""Shared, stdlib-only Anthropic Messages API client for the skill-evals harness.

Why this exists instead of `omp -p`: `omp` headless mode routes through the full
primary-agent runtime (rules, skills, memory recall, tool grant), which bleeds a
strong baked assistant persona into the output even with --no-tools --no-rules
--no-skills --no-lsp --no-extensions and a `memory.backend: none` config overlay
(verified 2026-08-24: the model still claimed to have searched Slack with zero
tool calls in the transcript). That contamination makes it useless for testing
an isolated advisor system prompt. Calling the Anthropic API directly with the
credential from `omp token anthropic` bypasses all of that and reproduces the
advisor's exact `nit`/`concern`/`blocker` voice cleanly (verified end-to-end).
"""

import json
import random
import subprocess
import time
import urllib.error
import urllib.request

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def get_anthropic_token(force_refresh: bool = False) -> str:
    """Fetch the Anthropic API key/OAuth token via `omp token anthropic`."""
    cmd = ["omp", "token", "anthropic"]
    if force_refresh:
        cmd.append("--force-refresh")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"`omp token anthropic` failed: {out.stderr.strip()}")
    token = out.stdout.strip()
    if not token:
        raise RuntimeError("`omp token anthropic` returned an empty token")
    return token


def call_anthropic(
    system: str,
    user_content: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    token: str | None = None,
    retries: int = 5,
) -> dict:
    """POST one Messages-API turn. Returns the parsed JSON response body.

    Retries once on 401 (refreshing the token) and up to `retries` times on
    429/5xx with capped exponential backoff plus jitter -- under the
    bounded-concurrency harness (run_eval.py/judge.py, --concurrency workers
    hitting the API in parallel), 429s from bursty concurrent load are
    expected and must be absorbed here rather than failing the case.
    """
    tok = token or get_anthropic_token()
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
        }
    ).encode()

    attempt = 0
    while True:
        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": tok,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            payload = e.read().decode(errors="replace")
            if e.code == 401 and attempt < retries:
                tok = get_anthropic_token(force_refresh=True)
                attempt += 1
                continue
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                delay = min(2**attempt, 20) + random.uniform(0, 1)
                time.sleep(delay)
                attempt += 1
                continue
            raise RuntimeError(f"Anthropic API error {e.code}: {payload[:500]}") from e


def extract_text(response: dict) -> str:
    """Pull the concatenated text blocks out of a Messages API response body."""
    parts = [b.get("text", "") for b in response.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()
