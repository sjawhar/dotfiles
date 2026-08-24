#!/usr/bin/env python3
"""Pull full context windows for skill-eval case stubs from raw session stores.

Input: a JSONL file of case stubs following the binding intervention-case schema
(id, source, session, project, intervention_ts, category, sami_message,
what_agent_was_doing, expected_flag, turning_point, lesson). `id` MUST be
"<session-id-fragment>:<turn>" where <turn> is the 1-based sequential message
index in the raw session (matching the reflect skill's "## [n] USER/ASSISTANT"
numbering) -- this is the primary locator; intervention_ts + sami_message are
used to cross-check/fall back when the turn number does not resolve cleanly.

Output: the same stub fields plus `context_window`: a list of {role, text} for
up to --context-turns raw turns immediately before the intervention, oldest
first, text-only (no tool calls/thinking), assistant text capped at 2000 chars.

Also supports `--negatives K`: for each session referenced in the stub input,
sample K points where Sami did NOT intervene (no stub turn within 5 raw turns),
emitted as category="none" cases for false-positive measurement.

Idempotent: re-running with the same --out skips ids already present unless
--overwrite is given.
"""

import argparse
import glob
import json
import os
import random
import sqlite3
import sys
from datetime import datetime

DEFAULT_OUT = os.path.expanduser("~/.dotfiles/.claude/skill-evals/cases.jsonl")
OMP_SESSIONS_GLOB = os.path.expanduser("~/.omp/agent/sessions/*/*.jsonl")
OPENCODE_SESSIONS_GLOB = os.path.expanduser("~/.local/share/opencode/sessions/*.db")
CLAUDE_CODE_SESSIONS_GLOB = os.path.expanduser("~/.dotfiles/.claude/projects/*/*.jsonl")

ASSISTANT_TEXT_CAP = 2000
NEGATIVE_EXCLUSION_RADIUS = 5


def normalize(s: str) -> str:
    return " ".join((s or "").split())


def parse_ts_ms(ts) -> int | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # opencode stores epoch ms already
        return int(ts)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def find_session_file(source: str, session_fragment: str) -> str | None:
    if source == "oh-my-pi":
        for path in glob.glob(OMP_SESSIONS_GLOB):
            if session_fragment in os.path.basename(path):
                return path
        return None
    if source == "opencode":
        for path in glob.glob(OPENCODE_SESSIONS_GLOB):
            if session_fragment in os.path.basename(path):
                return path
        return None
    if source == "claude-code":
        for path in glob.glob(CLAUDE_CODE_SESSIONS_GLOB):
            if session_fragment in os.path.basename(path):
                return path
        return None
    raise ValueError(f"unknown source: {source}")


def parse_omp_session(path: str) -> list[dict]:
    turns = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "message":
                continue
            msg = d.get("message", {})
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            texts = [c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text"]
            text = normalize("\n".join(t for t in texts if t))
            turns.append({"ts_ms": parse_ts_ms(d.get("timestamp")), "role": role, "text": text})
    return turns


def parse_opencode_session(path: str) -> list[dict]:
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, time_created, data FROM message ORDER BY time_created, id")
        rows = cur.fetchall()
        turns = []
        for mid, tc, data in rows:
            md = json.loads(data)
            role = md.get("role")
            if role not in ("user", "assistant"):
                continue
            pcur = con.cursor()
            pcur.execute("SELECT data FROM part WHERE message_id = ? ORDER BY id", (mid,))
            texts = []
            for (pdata,) in pcur.fetchall():
                pd = json.loads(pdata)
                if pd.get("type") == "text" and pd.get("text"):
                    texts.append(pd["text"])
            text = normalize("\n".join(texts))
            turns.append({"ts_ms": parse_ts_ms(tc), "role": role, "text": text})
        return turns
    finally:
        con.close()


def parse_claude_code_session(path: str) -> list[dict]:
    turns = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant"):
                continue
            if d.get("isSidechain"):
                continue
            msg = d.get("message", {})
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            texts = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                        texts.append(c["text"])
            text = normalize("\n".join(t for t in texts if t))
            turns.append({"ts_ms": parse_ts_ms(d.get("timestamp")), "role": role, "text": text})
    return turns


PARSERS = {
    "oh-my-pi": parse_omp_session,
    "opencode": parse_opencode_session,
    "claude-code": parse_claude_code_session,
}


def parse_session(source: str, path: str) -> list[dict]:
    return PARSERS[source](path)


def stub_turn_number(stub_id: str) -> int | None:
    try:
        return int(stub_id.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return None


MIN_MATCH_CHARS = 12


def texts_match(a: str, b: str) -> bool:
    """True if normalized texts `a` and `b` denote the same message: exact
    equality, or substring containment where the contained side is long
    enough (>= MIN_MATCH_CHARS) to be a meaningful match rather than noise.

    Empty/whitespace strings never match anything (including each other) --
    plain `x in y` containment lets an empty string trivially satisfy any
    check, and short strings ("1", "go") substring-match unrelated turns.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= MIN_MATCH_CHARS and a in b:
        return True
    if len(b) >= MIN_MATCH_CHARS and b in a:
        return True
    return False


def locate_intervention(turns: list[dict], stub: dict) -> int | None:
    """Resolve the raw turn index (0-based) of a stub's intervention message."""
    turn_no = stub_turn_number(stub["id"])
    sami_norm = normalize(stub.get("sami_message", ""))
    target_ts = parse_ts_ms(stub.get("intervention_ts"))

    # Fast path: trust the verified turn number in the id. Only cross-check
    # against sami_message when one is present; an empty sami_message can't
    # reject the fast path, but it also can't be used to justify a fallback.
    if turn_no is not None and 1 <= turn_no <= len(turns):
        idx = turn_no - 1
        cand = turns[idx]
        if cand["role"] == "user" and (not sami_norm or texts_match(sami_norm, cand["text"])):
            return idx

    # Fallback: nearest user turn by timestamp, cross-checked by text substring.
    user_idxs = [i for i, t in enumerate(turns) if t["role"] == "user"]
    if sami_norm:
        text_matches = [i for i in user_idxs if texts_match(sami_norm, turns[i]["text"])]
        if text_matches:
            if target_ts is not None:
                text_matches.sort(key=lambda i: abs((turns[i]["ts_ms"] or target_ts) - target_ts))
            return text_matches[0]
    if target_ts is not None and user_idxs:
        user_idxs.sort(key=lambda i: abs((turns[i]["ts_ms"] or target_ts) - target_ts))
        return user_idxs[0]
    return None


def build_context_window(turns: list[dict], idx: int, n: int) -> list[dict]:
    window = turns[max(0, idx - n) : idx]
    out = []
    for t in window:
        if not t["text"]:
            continue
        text = t["text"]
        if t["role"] == "assistant" and len(text) > ASSISTANT_TEXT_CAP:
            text = text[:ASSISTANT_TEXT_CAP] + " …[truncated]"
        out.append({"role": t["role"], "text": text})
    return out


def load_existing_ids(out_path: str) -> set[str]:
    ids = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return ids


def sample_negatives(turns: list[dict], positive_idxs: set[int], k: int, session_key: str, context_turns: int) -> list[int]:
    candidates = [
        i
        for i, t in enumerate(turns)
        if t["role"] == "user"
        and t["text"]
        and i > context_turns
        and all(abs(i - p) > NEGATIVE_EXCLUSION_RADIUS for p in positive_idxs)
    ]
    rng = random.Random(session_key)
    rng.shuffle(candidates)
    return candidates[:k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stubs", help="JSONL file of case stubs (binding schema)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"output case library JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--context-turns", type=int, default=15, help="raw turns to pull before the intervention (default: 15)")
    ap.add_argument("--negatives", type=int, default=0, help="negative samples per session (default: 0)")
    ap.add_argument("--overwrite", action="store_true", help="replace existing ids instead of skipping them")
    args = ap.parse_args()

    if not args.stubs:
        ap.error("--stubs is required (JSONL of case stubs; see SKILL.md)")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    existing_ids = set() if args.overwrite else load_existing_ids(args.out)

    stubs = []
    with open(args.stubs) as f:
        for line in f:
            line = line.strip()
            if line:
                stubs.append(json.loads(line))

    session_cache: dict[tuple[str, str], list[dict]] = {}
    written = 0
    skipped_existing = 0
    unresolved = []
    by_session: dict[tuple[str, str], list[dict]] = {}
    for stub in stubs:
        by_session.setdefault((stub["source"], stub["session"]), []).append(stub)

    out_records = []
    for (source, session), group in by_session.items():
        session_path = find_session_file(source, session)
        if session_path is None:
            for stub in group:
                unresolved.append((stub["id"], "session file not found"))
            continue
        turns = session_cache.setdefault((source, session), parse_session(source, session_path))

        positive_idxs = set()
        for stub in group:
            idx = locate_intervention(turns, stub)
            if idx is None:
                if stub["id"] not in existing_ids:
                    unresolved.append((stub["id"], "could not locate intervention turn"))
                continue
            positive_idxs.add(idx)
            if stub["id"] in existing_ids:
                skipped_existing += 1
                continue
            record = dict(stub)
            record["context_window"] = build_context_window(turns, idx, args.context_turns)
            out_records.append(record)
            written += 1

        if args.negatives > 0:
            neg_idxs = sample_negatives(turns, positive_idxs, args.negatives, f"{source}:{session}", args.context_turns)
            for i, idx in enumerate(neg_idxs):
                neg_id = f"{session[-12:]}:neg{idx}"
                if neg_id in existing_ids:
                    skipped_existing += 1
                    continue
                next_user_text = turns[idx]["text"]
                record = {
                    "id": neg_id,
                    "source": source,
                    "session": session,
                    "project": group[0].get("project", ""),
                    "intervention_ts": None,
                    "category": "none",
                    "sami_message": next_user_text,
                    "what_agent_was_doing": "",
                    "expected_flag": "",
                    "turning_point": False,
                    "lesson": "",
                    "context_window": build_context_window(turns, idx, args.context_turns),
                }
                out_records.append(record)
                written += 1

    with open(args.out, "a") as f:
        for record in out_records:
            f.write(json.dumps(record) + "\n")

    print(f"wrote {written} cases to {args.out} (skipped {skipped_existing} already present)", file=sys.stderr)
    if unresolved:
        print(f"unresolved: {len(unresolved)}", file=sys.stderr)
        for cid, reason in unresolved:
            print(f"  {cid}: {reason}", file=sys.stderr)
    return 0 if written > 0 or not stubs else 1


if __name__ == "__main__":
    raise SystemExit(main())
