#!/usr/bin/env python3
"""A `completion(prompt, model, system, schema)` compatible with the omp eval kernel's
built-in `completion()`, for scripts that must run unattended (no eval kernel): the
daily-measure job and, as a fallback, classify-sami-events.py.

Calls Anthropic's Messages API through the Hawk middleman gateway, the same route omp
itself uses (`providers.anthropic` in `~/.omp/agent/models.yml`, built from the
gitignored `omp/models.local.yml` -- the gateway URL is machine-local and is never
committed; it is read from that file at call time and this raises loudly if the file or
the key is missing). Auth is a fresh `hawk-token-fast` read per request (not cached in
this process): `hawk-token-refresh.service` keeps its on-disk cache warm every 3
minutes, so re-reading per request means a long run never holds a token past its
lifetime, and a cold cache falls back to a live mint (hawk-token-fast's own behavior).

MODEL_ID is this job's labeller of record: `claude-fable-5-1` at `xhigh` effort, the
model behind the 2026-10-04 weekly baseline (`omp/config.yml` modelRoles.default). Never
point this at a smol/flash/lite model: CLAUDE.md "No cheap/smol models anywhere
judgment is involved" applies to this labelling exactly as it does to the weekly run's
strong model.

The API rejects `tool_choice` of type `tool`/`any` and `thinking.type: "enabled"` for
this model -- this module uses neither. `output_config.format.schema` requires every
object-typed node to set `additionalProperties: false`; `_harden_schema` adds that
recursively without changing what a caller's schema means, so a caller's schema (e.g.
classify-sami-events.py's SCHEMA, experiments-readout.py's TURN_SCHEMA) is written for
the json-schema a human reads, not for this one gateway quirk.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import yaml

MODEL = os.environ.get("REFLECT_CLAUDE_MODEL", "claude-fable-5-1")
EFFORT = os.environ.get("REFLECT_CLAUDE_EFFORT", "xhigh")
# Stored on every label row: names both model and effort, so a change to either shows
# up as a `model` change in the series, same as a model swap always has.
MODEL_ID = f"{MODEL}:{EFFORT}"

MODELS_YML_PATH = os.path.expanduser(
    os.environ.get("REFLECT_MODELS_YML", "~/.omp/agent/models.yml")
)
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0


class Refusal(RuntimeError):
    """The model declined to answer this specific prompt (`stop_reason: "refusal"` or
    any response with no text content) -- distinct from RuntimeError's other uses here
    (a missing models.yml/hawk-token-fast, an exhausted HTTP retry), which must still
    propagate and fail the run loudly. A caller labelling many independent items
    retries or isolates (bisects) on this one, never on the infra failures above."""


def _base_url() -> str:
    try:
        with open(MODELS_YML_PATH) as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise RuntimeError(
            f"{MODELS_YML_PATH} is unreadable ({e}); this process needs "
            "providers.anthropic.baseUrl from omp's merged models.yml to reach the "
            "Hawk gateway"
        ) from e
    base_url = ((data or {}).get("providers") or {}).get("anthropic", {}).get("baseUrl")
    if not base_url:
        raise RuntimeError(
            f"{MODELS_YML_PATH} has no providers.anthropic.baseUrl; omp/models.local.yml "
            "(gitignored, machine-local) must set it for this box to reach the gateway"
        )
    return base_url


def _hawk_token() -> str:
    try:
        return subprocess.check_output(["hawk-token-fast"], timeout=30).decode().strip()
    except FileNotFoundError as e:
        raise RuntimeError(
            "hawk-token-fast is not on PATH; this process needs it (and "
            "~/.dotfiles/shims:~/.dotfiles/scripts on PATH) to authenticate to the "
            "Hawk gateway"
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"hawk-token-fast failed (exit {e.returncode}): {e.output}") from e


def _harden_schema(schema):
    """Recursive `additionalProperties: false` on every object-typed node -- the
    gateway's json_schema format demands it explicitly; this never changes what the
    schema means to a caller reading it."""
    if isinstance(schema, dict):
        out = {k: _harden_schema(v) for k, v in schema.items()}
        if out.get("type") == "object" and "properties" in out:
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_harden_schema(v) for v in schema]
    return schema


class _Handle:
    """Mimics the eval kernel's completion() handle: a synchronous `.wait()` that
    returns the response text (here, already JSON because of output_config.format)."""

    def __init__(self, prompt: str, system: str | None, schema: dict | None):
        self.prompt = prompt
        self.system = system
        self.schema = schema

    def wait(self) -> str:
        base_url = _base_url()
        body = {
            "model": MODEL,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": self.prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": EFFORT},
        }
        if self.system:
            body["system"] = self.system
        if self.schema:
            body["output_config"]["format"] = {
                "type": "json_schema",
                "schema": _harden_schema(self.schema),
            }
        url = f"{base_url}/v1/messages"

        last_err = None
        for attempt in range(MAX_RETRIES):
            token = _hawk_token()
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": token,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:800]
                last_err = RuntimeError(f"Claude API error {e.code}: {detail}")
                if e.code != 429 and not (500 <= e.code < 600):
                    raise last_err
            except urllib.error.URLError as e:
                last_err = RuntimeError(f"Claude API unreachable: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        else:
            raise last_err
        try:
            blocks = data["content"]
        except KeyError as e:
            raise RuntimeError(f"Claude API returned no content: {json.dumps(data)[:800]}") from e
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise Refusal(
                f"Claude returned no text content (stop_reason={data.get('stop_reason')!r}); "
                "this is the model declining to answer this specific prompt, observed on "
                "real Dispatch/turn content describing red-team offensive-security work -- "
                "not a transient error, and a caller labelling many items should isolate "
                "this one rather than retry it unchanged"
            )
        return text


def completion(prompt: str, model: str = "default", system: str | None = None,
               schema: dict | None = None) -> _Handle:
    """Signature-compatible with the eval kernel's completion(); `model` is accepted and
    ignored (every caller gets MODEL/EFFORT -- there is no smol/slow tier here, by
    design)."""
    return _Handle(prompt, system, schema)
