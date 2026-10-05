#!/usr/bin/env python3
"""Jev (TypeSafe System One) turn labelling: a comparison labeller, never the series.

Measured 2026-10-04 against 299 of Sami's turns labelled by the strong model
(`experiments-readout.TURN_SYSTEM`/`TURN_SCHEMA`): on the items Jev answers with the
SDK's own `confidence` >= 0.5 (91.6% of turns), Jev's choice matches the strong-model
label 91.2% of the time (284/299 overall vs the strong labels; A-vs-reversed-option-order
agreement 96.3%, so the remaining disagreement is not an artifact of option order).
Full writeup: `.claude/session-analysis/2026-10-04-work/jev-evaluation.md`.

Two output files, independent and both optional (pass either or both):
- `--out`: `{id, label}`, confidence>=threshold items only. The weekly
  experiments-readout.py's Jev-first, strong-model-escalated pre-pass
  (`label_turns`/`jev_labels_path`) reads this shape unchanged.
- `--out-all`: `{id, label, confidence}`, every turn Jev answers regardless of
  confidence. daily-measure.py's turn phase reads this to populate
  `reflect-store.py`'s `turn_labels_alt` -- Jev's opinion archived for comparison
  against the Claude series, never fed into `turn_labels`/`daily_rates`.

Standalone script (not loaded into the eval kernel): the TypeSafe SDK needs
TYPESAFE_API_KEY, which `scripts/secret-run` (the agent-secrets broker for a
registered session, secretsd's agent tier otherwise -- never `secrets` directly, see
that script's header) injects as TYPESAFE_AI_API_KEY into a subprocess's environment,
not into the persistent eval kernel. Run it between two eval cells for the weekly
experiments readout (daily-measure.py's turn phase runs the same two steps unattended):

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


async def run(turns_path, out_path, out_all_path=None, confidence_threshold=CONFIDENCE_THRESHOLD,
              concurrency=16):
    turns = [json.loads(line) for line in Path(turns_path).expanduser().open()]
    out_path = Path(out_path).expanduser() if out_path else None
    out_all_path = Path(out_all_path).expanduser() if out_all_path else None
    done = set()
    done_source = out_all_path if out_all_path is not None else out_path
    if done_source is not None and done_source.exists():
        for line in done_source.open():
            done.add(json.loads(line)["id"])
    todo = [t for t in turns if t["id"] not in done]
    print(f"{len(done)} already answered, {len(todo)} to classify", file=sys.stderr)

    sem = asyncio.Semaphore(concurrency)
    out_f = out_path.open("a") if out_path else None
    out_all_f = out_all_path.open("a") if out_all_path else None
    lock = asyncio.Lock()
    n_confident = 0
    n_escalated = 0
    n_failed = 0
    first_error = None

    async with AsyncTypeSafeClient(model="jev-latest") as client:
        async def one(t):
            nonlocal n_confident, n_escalated, n_failed, first_error
            for attempt in range(3):
                try:
                    async with sem:
                        result = await asyncio.wait_for(
                            client.system_one(t["text"], build_question()), timeout=60
                        )
                    break
                except Exception as exc:
                    if first_error is None:
                        first_error = f"{type(exc).__name__}: {exc}"
                    await asyncio.sleep(1.5 * (attempt + 1))
            else:
                n_escalated += 1
                n_failed += 1
                return
            ans = result.choices["label"]
            async with lock:
                if out_all_f is not None:
                    out_all_f.write(json.dumps(
                        {"id": t["id"], "label": ans.choice, "confidence": ans.confidence}
                    ) + "\n")
                    out_all_f.flush()
                if ans.confidence >= confidence_threshold:
                    if out_f is not None:
                        out_f.write(json.dumps({"id": t["id"], "label": ans.choice}) + "\n")
                        out_f.flush()
                    n_confident += 1
                else:
                    n_escalated += 1

        await asyncio.gather(*(one(t) for t in todo))
    if out_f is not None:
        out_f.close()
    if out_all_f is not None:
        out_all_f.close()
    print(
        f"Jev answered {n_confident} turns at confidence>={confidence_threshold}"
        + (f" (written to {out_path})" if out_path else "")
        + f"; {n_escalated} below that bar"
        + (f" (written to {out_all_path} with the rest)" if out_all_path else " (not written anywhere)"),
        file=sys.stderr,
    )
    if n_failed:
        print(
            f"of those, {n_failed} left unanswered because every call for them failed, "
            f"first error: {first_error} - check TYPESAFE_API_KEY and the model name "
            f"before reading this run as low confidence",
            file=sys.stderr,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", required=True, help="jsonl of {id, text}")
    ap.add_argument("--out", default=None, help="jsonl of {id, label}; confidence>=threshold items only")
    ap.add_argument("--out-all", default=None,
                     help="jsonl of {id, label, confidence}; every turn Jev answers, regardless of "
                     "confidence (comparison use, e.g. daily-measure.py's turn_labels_alt)")
    ap.add_argument("--confidence-threshold", type=float, default=CONFIDENCE_THRESHOLD)
    ap.add_argument("--concurrency", type=int, default=16)
    args = ap.parse_args()
    if not args.out and not args.out_all:
        raise SystemExit("jev-turn-label.py: pass --out, --out-all, or both")
    asyncio.run(run(args.turns, args.out, args.out_all, args.confidence_threshold, args.concurrency))



if __name__ == "__main__":
    main()
