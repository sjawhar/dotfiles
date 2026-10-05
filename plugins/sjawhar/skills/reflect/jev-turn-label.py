#!/usr/bin/env python3
"""Confidence-routed Jev (TypeSafe System One) pre-pass for turn labelling.

Measured 2026-10-04 against 299 of Sami's turns labelled by the strong model
(`experiments-readout.TURN_SYSTEM`/`TURN_SCHEMA`): on the items Jev answers with the
SDK's own `confidence` >= 0.5 (91.6% of turns), Jev's choice matches the strong-model
label 91.2% of the time (284/299 overall vs the strong labels; A-vs-reversed-option-order
agreement 96.3%, so the remaining disagreement is not an artifact of option order).
Items below the confidence bar (8.4%) are left out of `--out` entirely — this is an
explicit escalation, not a silent fallback: `experiments-readout.label_turns` sends
exactly those to the strong model's `completion()`, unchanged from before this pre-pass
existed. Full writeup: `.claude/session-analysis/2026-10-04-work/jev-evaluation.md`.

Standalone script (not loaded into the eval kernel): the TypeSafe SDK needs
TYPESAFE_API_KEY, which `scripts/secret-run` only injects into a subprocess's
environment, not into the persistent eval kernel. Run it between two eval cells:

    # eval cell 1
    %load ~/.dotfiles/plugins/sjawhar/skills/reflect/experiments-readout.py
    dump_turns_for_jev(session_dirs=[...], prompts=[...], out_path="turns.jsonl")

    # bash
    secret-run TYPESAFE_AI_API_KEY -- sh -c '
      export TYPESAFE_API_KEY=$TYPESAFE_AI_API_KEY
      uv run --with typesafe-sdk python3 ~/.dotfiles/plugins/sjawhar/skills/reflect/jev-turn-label.py \
        --turns turns.jsonl --out jev-labels.jsonl
    '

    # eval cell 2
    await main(..., jev_labels_path="jev-labels.jsonl")
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice

CONFIDENCE_THRESHOLD = 0.5

CRITERIA = {
    "correction": (
        "Sami tells the agent it did something wrong, misunderstood, made something "
        "up, did what he did not ask for, should not have done something, or repeats "
        "an instruction it should already have followed. Rebukes and redirections of "
        "the agent's own behavior count. THIS INCLUDES a frustrated or rhetorical "
        "question whose literal answer would be an admission of failure or delay -- "
        "'Why do we still have no X?', 'Have you fixed it yet?', 'Why is X slipping "
        "through the cracks?', 'Why is X running on Y instead of Z?' -- even though "
        "it ends in a question mark and never states 'you did X wrong' outright: "
        "read these the way an impatient, annoyed human reads them, not as a neutral "
        "request for facts."
    ),
    "other": (
        "Sami's new request, information, answer, approval, or a genuine, "
        "even-toned question seeking facts or clarification -- NOT a rhetorical or "
        "frustrated question implying the agent failed or is late (that is "
        "correction)."
    ),
    "not_sami": (
        "Text a harness, script, scheduled runner, probe, or another agent wrote, "
        "not Sami. This includes a multi-step task brief addressed to an agent in "
        "the second person with structural markers like 'You own <issue> end to "
        "end', numbered steps, 'Claim the issue', 'dispatch_claim', naming another "
        "session to message -- these are legion/orchestrator-authored briefs even "
        "though they read as fluent directives. Also: scheduled ingest prompts, "
        "scripted test instructions ('Reply with exactly ...', 'Use the task tool "
        "to spawn exactly ONE subagent ...'), session moved or restarted notices, a "
        "lone '.' nudge."
    ),
}

INSTRUCTIONS = (
    "This is one turn typed into an AI coding agent's session by Sami, the human who "
    "runs a fleet of coding agents (or by a harness/script standing in for him). "
    "Which of these three best describes it?"
)


def build_question():
    return {"label": Choice(instructions=INSTRUCTIONS, criteria=dict(CRITERIA))}


async def run(turns_path, out_path, confidence_threshold=CONFIDENCE_THRESHOLD, concurrency=16):
    turns = [json.loads(line) for line in Path(turns_path).expanduser().open()]
    out_path = Path(out_path).expanduser()
    done = set()
    if out_path.exists():
        for line in out_path.open():
            done.add(json.loads(line)["id"])
    todo = [t for t in turns if t["id"] not in done]
    print(f"{len(done)} already routed, {len(todo)} to classify", file=sys.stderr)

    sem = asyncio.Semaphore(concurrency)
    out_f = out_path.open("a")
    lock = asyncio.Lock()
    n_confident = 0
    n_escalated = 0

    async with AsyncTypeSafeClient(model="jev-latest") as client:
        async def one(t):
            nonlocal n_confident, n_escalated
            for attempt in range(3):
                try:
                    async with sem:
                        result = await asyncio.wait_for(
                            client.system_one(t["text"], build_question()), timeout=60
                        )
                    break
                except Exception:
                    await asyncio.sleep(1.5 * (attempt + 1))
            else:
                n_escalated += 1
                return
            ans = result.choices["label"]
            async with lock:
                if ans.confidence >= confidence_threshold:
                    out_f.write(json.dumps({"id": t["id"], "label": ans.choice}) + "\n")
                    out_f.flush()
                    n_confident += 1
                else:
                    n_escalated += 1

        await asyncio.gather(*(one(t) for t in todo))
    out_f.close()
    print(
        f"Jev answered {n_confident} turns at confidence>={confidence_threshold}; "
        f"escalating {n_escalated} to the strong model (not written to {out_path})",
        file=sys.stderr,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", required=True, help="jsonl of {id, text}")
    ap.add_argument("--out", required=True, help="jsonl of {id, label}; confident items only")
    ap.add_argument("--confidence-threshold", type=float, default=CONFIDENCE_THRESHOLD)
    ap.add_argument("--concurrency", type=int, default=16)
    args = ap.parse_args()
    asyncio.run(run(args.turns, args.out, args.confidence_threshold, args.concurrency))


if __name__ == "__main__":
    main()
