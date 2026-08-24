#!/usr/bin/env python3
"""Run the omp advisor's exact system prompt against a skill-eval case library.

Assembles the real advisor system prompt the same way omp does at runtime
(packages/coding-agent/src/session/session-advisors.ts): the advisor system.md
verbatim, plus the watchdog file under test wrapped in omp's exact wrapper
format ("Especially pay attention to:\\n<attention>...\\n</attention>" --
packages/coding-agent/src/advisor/watchdog.ts:discoverWatchdogFiles).

Model invocation deliberately does NOT use `omp -p`: verified 2026-08-24 that
headless omp bleeds a baked assistant persona into any custom --system-prompt
even with --no-tools --no-rules --no-skills --no-lsp --no-extensions and a
`memory.backend: none` config overlay (it hallucinated a Slack search with zero
tool calls in the transcript). Instead this calls the Anthropic Messages API
directly with the credential from `omp token anthropic`, which reproduces the
advisor's real nit/concern/blocker voice cleanly. See _anthropic.py.

For each case, the case's `context_window` is rendered as a transcript
increment (oldest turn first) and fed as the advisor's next update, with a
short harness-only trailing instruction (NOT part of the real system prompt)
asking it to answer as it would via the `advise` tool, since this harness has
no `advise` tool wired for it to call.
"""

import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _anthropic import DEFAULT_MODEL, call_anthropic, extract_text, get_anthropic_token  # noqa: E402

DEFAULT_CASES = os.path.expanduser("~/.dotfiles/.claude/skill-evals/cases.jsonl")
DEFAULT_WATCHDOG = os.path.expanduser("~/.omp/agent/WATCHDOG.md")
DEFAULT_OUT = os.path.expanduser("~/.dotfiles/.claude/skill-evals/results.jsonl")
ADVISOR_SYSTEM_PROMPT = os.path.expanduser(
    "~/oh-my-pi/default/packages/coding-agent/src/prompts/advisor/system.md"
)

HARNESS_SUFFIX = (
    "\n\n<harness-instruction>\n"
    "This is a transcript excerpt from the primary agent's session, ending right "
    "before a turn you have not seen yet. Respond exactly as you would through "
    "the `advise` tool: one line naming the level (nit/concern/blocker), then "
    "your note. If nothing in this update rises to that bar, reply with exactly: "
    "NONE\n</harness-instruction>"
)


def build_system_prompt(watchdog_path: str) -> str:
    sysmd = open(ADVISOR_SYSTEM_PROMPT).read().strip()
    watchdog_text = open(watchdog_path).read().strip()
    attention = f"Especially pay attention to:\n<attention>\n{watchdog_text}\n</attention>"
    return f"{sysmd}\n\n{attention}"


def render_transcript(context_window: list[dict]) -> str:
    parts = []
    for turn in context_window:
        header = "## USER" if turn["role"] == "user" else "## ASSISTANT"
        parts.append(f"{header}\n{turn['text']}")
    return "\n\n".join(parts) + HARNESS_SUFFIX


def run_one(case: dict, args, system_prompt: str, token: str, out_f, write_lock: threading.Lock) -> dict:
    transcript = render_transcript(case.get("context_window", []))
    start = time.monotonic()
    record = {
        "id": case["id"],
        "category": case.get("category"),
        "model": args.model,
        "watchdog": args.watchdog,
    }
    try:
        resp = call_anthropic(system_prompt, transcript, model=args.model, max_tokens=args.max_tokens, token=token)
        record["raw_output"] = extract_text(resp)
        record["latency_ms"] = int((time.monotonic() - start) * 1000)
        record["usage"] = resp.get("usage")
    except Exception as e:  # noqa: BLE001 - surface any provider/network failure per-case
        record["error"] = str(e)
        record["latency_ms"] = int((time.monotonic() - start) * 1000)
    with write_lock:
        out_f.write(json.dumps(record) + "\n")
        out_f.flush()
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=DEFAULT_CASES, help=f"case library JSONL (default: {DEFAULT_CASES})")
    ap.add_argument("--watchdog", default=DEFAULT_WATCHDOG, help=f"watchdog file under test (default: {DEFAULT_WATCHDOG})")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"results JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Anthropic model id (default: {DEFAULT_MODEL})")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--limit", type=int, default=None, help="cap number of cases run (smoke testing)")
    ap.add_argument("--append", action="store_true", help="append to --out instead of replacing it")
    ap.add_argument("--ids", nargs="*", help="only run cases with these ids")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel API calls in flight (default: 8)")
    args = ap.parse_args()

    if not os.path.exists(args.cases):
        ap.error(f"case library not found: {args.cases}")
    if not os.path.exists(args.watchdog):
        ap.error(f"watchdog file not found: {args.watchdog}")

    system_prompt = build_system_prompt(args.watchdog)

    cases = []
    with open(args.cases) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if args.ids:
        wanted = set(args.ids)
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    token = get_anthropic_token()
    mode = "a" if args.append else "w"
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    n_ok, n_err = 0, 0
    write_lock = threading.Lock()
    with open(args.out, mode) as out_f:
        with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(run_one, case, args, system_prompt, token, out_f, write_lock) for case in cases]
            for fut in cf.as_completed(futs):
                record = fut.result()
                ok = "error" not in record
                n_ok += int(ok)
                n_err += int(not ok)
                print(f"[{record.get('category', '?')}] {record['id']}: " + ("ok" if ok else f"ERROR {record['error'][:120]}"), file=sys.stderr)

    print(f"ran {len(cases)} cases ({n_ok} ok, {n_err} errors) -> {args.out}", file=sys.stderr)
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
