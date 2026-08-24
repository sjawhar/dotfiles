#!/usr/bin/env python3
"""LLM-judge run_eval.py results against each case's expected_flag, plus
deterministic aggregate metrics (catch rate per category, false-flag rate).

For category != "none" cases: judged CAUGHT / PARTIAL / MISSED by comparing the
advisor's raw_output against expected_flag + sami_message via one LLM call.

For category == "none" cases: judged CLEAN / FALSE_FLAG deterministically (no
LLM call needed) -- silence (raw_output normalizes to "NONE") is CLEAN, any
other advisor note is a FALSE_FLAG. This is a real judgment call worth stating
plainly: it treats every non-silent advisor note on a true-negative window as
a false flag, even a reasonable-sounding one, because the case was selected
specifically because Sami did not react to anything in it -- that is the
contract this measures.

Prints a plain-text aggregate table and writes per-case verdicts to --out.
"""

import argparse
import concurrent.futures as cf
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _anthropic import DEFAULT_MODEL, call_anthropic, extract_text, get_anthropic_token  # noqa: E402

DEFAULT_RESULTS = os.path.expanduser("~/.dotfiles/.claude/skill-evals/results.jsonl")
DEFAULT_CASES = os.path.expanduser("~/.dotfiles/.claude/skill-evals/cases.jsonl")
DEFAULT_OUT = os.path.expanduser("~/.dotfiles/.claude/skill-evals/judged.jsonl")

JUDGE_SYSTEM = """You are grading whether an AI advisor's note caught a specific known problem \
in a coding-agent transcript. Respond with EXACTLY one verdict word on the first line \
(CAUGHT, PARTIAL, or MISSED), then a one-sentence justification on the second line. No \
other text.

CAUGHT: the advisor's note identifies the same core problem described in \
<expected_flag>, in substance (wording may differ).
PARTIAL: the advisor raised a real, relevant issue but missed or diluted the core \
problem in <expected_flag>.
MISSED: the advisor was silent (NONE), or its note addresses something unrelated to \
<expected_flag>."""

POSITIVE_CATEGORIES_VERDICTS = {"CAUGHT", "PARTIAL", "MISSED"}


def normalize_silence(text: str) -> bool:
    return text.strip().strip(".").upper() == "NONE"


def judge_positive(case: dict, result: dict, model: str, token: str) -> tuple[str, str]:
    user = (
        f"<what_agent_was_doing>{case.get('what_agent_was_doing', '')}</what_agent_was_doing>\n"
        f"<expected_flag>{case.get('expected_flag', '')}</expected_flag>\n"
        f"<sami_message>{case.get('sami_message', '')}</sami_message>\n"
        f"<advisor_output>{result.get('raw_output', '')}</advisor_output>"
    )
    resp = call_anthropic(JUDGE_SYSTEM, user, model=model, max_tokens=200, token=token)
    text = extract_text(resp)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    verdict = lines[0].upper() if lines else "MISSED"
    if verdict not in POSITIVE_CATEGORIES_VERDICTS:
        verdict = "MISSED"
    justification = lines[1] if len(lines) > 1 else text
    return verdict, justification


def judge_one(result: dict, case: dict, model: str, token: str) -> dict:
    if "error" in result:
        return {**result, "verdict": "ERROR", "justification": result["error"]}

    category = case.get("category", "other")
    raw_output = result.get("raw_output", "")
    if category == "none":
        verdict = "CLEAN" if normalize_silence(raw_output) else "FALSE_FLAG"
        justification = "silent (NONE)" if verdict == "CLEAN" else "raised a note on a no-intervention window"
    else:
        verdict, justification = judge_positive(case, result, model, token)

    return {
        "id": result["id"],
        "category": category,
        "turning_point": case.get("turning_point"),
        "verdict": verdict,
        "justification": justification,
        "raw_output": raw_output,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=DEFAULT_RESULTS, help=f"run_eval.py output JSONL (default: {DEFAULT_RESULTS})")
    ap.add_argument("--cases", default=DEFAULT_CASES, help=f"case library JSONL (default: {DEFAULT_CASES})")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"judged output JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"judge model id (default: {DEFAULT_MODEL})")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel judge calls in flight (default: 8)")
    args = ap.parse_args()

    cases_by_id = {}
    with open(args.cases) as f:
        for line in f:
            line = line.strip()
            if line:
                c = json.loads(line)
                cases_by_id[c["id"]] = c

    results = []
    with open(args.results) as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))

    token = get_anthropic_token()

    jobs = []
    for result in results:
        case = cases_by_id.get(result["id"])
        if case is None:
            print(f"skipping {result['id']}: no matching case in library", file=sys.stderr)
            continue
        jobs.append((result, case))

    judged: list[dict | None] = [None] * len(jobs)
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(judge_one, result, case, args.model, token): i for i, (result, case) in enumerate(jobs)}
        for fut in cf.as_completed(futs):
            i = futs[fut]
            row = fut.result()
            judged[i] = row
            print(f"{row['id']}: {row['verdict']}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        for row in judged:
            f.write(json.dumps(row) + "\n")

    print_table(judged)
    return 0


def print_table(judged: list[dict]) -> None:
    by_category: dict[str, dict[str, int]] = {}
    for row in judged:
        cat = row["category"] or "other"
        by_category.setdefault(cat, {}).setdefault(row["verdict"], 0)
        by_category[cat][row["verdict"]] += 1

    print()
    print(f"{'category':<20} {'n':>4} {'CAUGHT':>7} {'PARTIAL':>8} {'MISSED':>7} {'CLEAN':>6} {'FALSE_FLAG':>11} {'ERROR':>6}  {'catch/clean rate':>17}")
    print("-" * 100)
    for cat, counts in sorted(by_category.items()):
        n = sum(counts.values())
        caught, partial, missed = counts.get("CAUGHT", 0), counts.get("PARTIAL", 0), counts.get("MISSED", 0)
        clean, false_flag = counts.get("CLEAN", 0), counts.get("FALSE_FLAG", 0)
        error = counts.get("ERROR", 0)
        if cat == "none":
            denom = clean + false_flag
            rate = f"{clean / denom:.0%} clean" if denom else "n/a"
        else:
            denom = caught + partial + missed
            rate = f"{caught / denom:.0%} caught" if denom else "n/a"
        print(f"{cat:<20} {n:>4} {caught:>7} {partial:>8} {missed:>7} {clean:>6} {false_flag:>11} {error:>6}  {rate:>17}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
