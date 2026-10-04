#!/usr/bin/env python3
"""A `completion(prompt, model, system, schema)` compatible with the omp eval kernel's
built-in `completion()`, for scripts that must run unattended (no eval kernel): the
daily-measure job and, as a fallback, classify-sami-events.py.

Calls Google's Gemini API directly over HTTPS with structured JSON output
(`responseSchema`), authenticated with `GEMINI_API_KEY` from the environment --
`secret-run GEMINI_API_KEY -- ...` (the agent-secrets broker's grantable key, or
secretsd's agent tier when no broker identity is live; see daily-measure.py's module
docstring for why GEMINI_API_KEY and TYPESAFE_AI_API_KEY are the only model-provider
keys the broker currently grants, and ANTHROPIC_API_KEY/OPENAI_API_KEY are not).

GEMINI_MODEL is the strongest non-flash, non-lite model the gateway's key currently
serves (`gemini-3.1-pro-preview` as of 2026-10-04, confirmed against
generativelanguage.googleapis.com/v1beta/models). Never point this at a flash/lite
model: CLAUDE.md "No cheap/smol models anywhere judgment is involved" applies to this
labelling exactly as it does to the weekly run's strong model.
"""
import json
import os
import urllib.error
import urllib.request

GEMINI_MODEL = os.environ.get("REFLECT_GEMINI_MODEL", "gemini-3.1-pro-preview")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class _Handle:
    """Mimics the eval kernel's completion() handle: a synchronous `.wait()` that
    returns the response text (here, already JSON because of responseSchema)."""

    def __init__(self, prompt: str, system: str | None, schema: dict | None, model: str):
        self.prompt = prompt
        self.system = system
        self.schema = schema
        self.model = model

    def wait(self) -> str:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set; run this under `secret-run GEMINI_API_KEY -- ...` "
                "(scripts/secret-run: the agent-secrets broker for a registered session, "
                "secretsd's agent tier otherwise)"
            )
        body = {"contents": [{"role": "user", "parts": [{"text": self.prompt}]}]}
        if self.system:
            body["systemInstruction"] = {"parts": [{"text": self.system}]}
        if self.schema:
            body["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": self.schema,
            }
        url = f"{GEMINI_URL.format(model=self.model)}?key={key}"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        last_err = None
        for _attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                last_err = RuntimeError(f"Gemini API error {e.code}: {e.read().decode()[:800]}")
            except urllib.error.URLError as e:
                last_err = RuntimeError(f"Gemini API unreachable: {e}")
        else:
            raise last_err
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Gemini API returned no candidate: {json.dumps(data)[:800]}") from e
        return "".join(p.get("text", "") for p in parts if "text" in p)


def completion(prompt: str, model: str = "default", system: str | None = None,
               schema: dict | None = None) -> _Handle:
    """Signature-compatible with the eval kernel's completion(); `model` is accepted and
    ignored (every caller gets GEMINI_MODEL -- there is no smol/slow tier here, by design)."""
    return _Handle(prompt, system, schema, GEMINI_MODEL)
