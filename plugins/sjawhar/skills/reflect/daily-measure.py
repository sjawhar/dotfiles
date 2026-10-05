#!/usr/bin/env python3
"""Daily incremental version of the reflect skill's two label corpora: extend
classify-sami-events.py's Dispatch-event labels and jev-turn-label.py's turn labels by
whatever is new since the last successful run, store everything in reflect-store.py's
SQLite store, and recompute daily_rates. Read by daily-readout.py and by the weekly
reflect run (SKILL.md step 2), so a fix's effect on a failure class shows within days
instead of only at the next weekly sitting.

One series, one labeller: the 2,352-event seed (classify-sami-events.py's weekly run,
2026-10-04, the omp eval kernel's `completion()` default model) is relabelled from
scratch by `cmd_seed` with this job's own standalone model before anything is stored
as the series, so `daily_rates` never has a day where the rate moved because the
labeller changed rather than the fleet did. The displaced kernel-model opinion is kept
in `dispatch_labels_alt` for comparison (`reflect-store.py agreement`), never read by
`daily_rates`. See `reflect-store.py`'s module docstring for the schema this enforces.

Runs unattended under omp/reflect-daily.service (installers/reflect-daily.sh): no eval
kernel, so every model call is direct HTTP (standalone-model.py: Gemini) or the
TypeSafe SDK (jev-turn-label.py, unchanged), authenticated through
`secret-run GEMINI_API_KEY TYPESAFE_AI_API_KEY -- ...`.

Credentials (the "make it daily" item 2 question: can an unattended unit reach a key
through the agent-secrets broker, the same one `scripts/agent-secrets-session` gives a
host agent session): yes, proven 2026-10-04 by wrapping
`scripts/agent-secrets-session scripts/secret-run GEMINI_API_KEY TYPESAFE_AI_API_KEY --`
around a command run under `env -i` (no inherited session environment -- the same blank
slate systemd hands a user unit) and getting both keys back with exit 0, no human
approval (the broker's grants carry `approver: null` for these two). But the broker
(AGENTC-393) grants secrets from its own catalog, separate from secretsd's full list:
`agent-secrets ANTHROPIC_API_KEY -- true` and `agent-secrets OPENAI_API_KEY -- true` both
fail `UNKNOWN_SECRET` -- the broker has never heard of those names, though secretsd's
agent tier has both. So this job's strong-model step calls Gemini
(`gemini-3.1-pro-preview`, standalone-model.py), the only broker-grantable
judgment-capable key, and TypeSafe/Jev (jev-turn-label.py) for the cheap pre-pass
(also broker-grantable). It does not call Anthropic or OpenAI and does not fall back to
a personal credential.

Resumability: each of the three streams (Dispatch events, sami-agents turns,
devbox-agents-2 turns) tracks its own `job_state` watermark and only advances it after
that stream's fetch + label + commit fully succeeds, so a mid-run failure (an
unreachable key, a dead ssh host, a Dispatch API outage) leaves every already-succeeded
stream's progress intact and only the failed one is retried from the same point next
run. Every insert is keyed by a stable id (the Dispatch event's own id; `session|ts` for
a turn) via `INSERT OR IGNORE`, so re-fetching an overlapping window is a no-op, never a
duplicate row or a re-spent model call. Any stream's failure makes this process exit
non-zero -- never caught and continued -- so the systemd unit goes `failed` and the
journal shows exactly which stream and why.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOTFILES_DIR = Path(os.environ.get("REFLECT_DOTFILES_DIR", HERE.parents[3]))

# First-run defaults: Dispatch labels seed from the 2026-10-04 run (SKILL.md), so the
# dispatch stream always has a watermark by the time this runs for real. Turns have no
# seed (no backfill asked for), so a cold start measures only the last day, not two
# weeks of historical turns through a paid model call.
TURNS_FIRST_RUN_LOOKBACK_HOURS = 24
# Re-fetch this much before the last watermark on every run: turn/event ids are stable,
# so inserts of already-stored rows are no-ops (INSERT OR IGNORE) -- the overlap only
# guards minute-resolution cutoffs and eventual-consistency lag, never a duplicate.
OVERLAP_HOURS = 2

SAMI_AGENTS_HOST = "sami-agents"
DEVBOX_AGENTS_2_HOST = "devbox-agents-2"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("reflect_store", "reflect-store.py")
classify = _load("classify_sami_events", "classify-sami-events.py")
dispatch_extract = _load("extract_dispatch_human", "extract-dispatch-human.py")
experiments = _load("experiments_readout", "experiments-readout.py")
standalone_model = _load("standalone_model", "standalone-model.py")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def minus(ts_iso: str, hours: float) -> str:
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (dt - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --- (a) Dispatch events -----------------------------------------------------------

def fetch_sami_events_since(since_iso: str) -> list[dict]:
    keys: list[str] = []
    for project in dispatch_extract.projects():
        keys += dispatch_extract.issues(project, since_iso)
    with ThreadPoolExecutor(16) as pool:
        all_events = [e for evs in pool.map(dispatch_extract.events, keys) for e in evs]
    return sorted(
        (e for e in all_events
         if (e.get("actor") or {}).get("kind") == "user"
         and (e.get("actor") or {}).get("id", "").startswith("sami@")
         and e["type"] in dispatch_extract.KEEP
         and e["created_at"] >= since_iso),
        key=lambda e: e["created_at"],
    )


def run_dispatch_phase(conn) -> dict:
    since = store.get_state(conn, "dispatch_since")
    if since is None:
        raise RuntimeError(
            "job_state['dispatch_since'] is unset; run `daily-measure.py seed` first "
            "(SKILL.md step 2) to seed dispatch_events/dispatch_labels and set the watermark"
        )
    events = fetch_sami_events_since(since)
    new_events = [e for e in events if not store.dispatch_event_has_label(conn, e["id"])]
    labeled = 0
    unlabeled_created_at: list[str] = []
    if new_events:
        items = [classify.build_item(e["id"], e) for e in new_events]
        by_id = asyncio.run(classify.classify_all(items, model="default"))
        for e in new_events:
            labels = by_id.get(e["id"])
            if labels is None:
                unlabeled_created_at.append(e["created_at"])
                continue
            store.upsert_dispatch_event(conn, e)
            store.upsert_dispatch_label(conn, e["id"], labels, "gemini-3.1-pro-preview", now_iso())
            labeled += 1
        if unlabeled_created_at:
            print(f"dispatch: {len(unlabeled_created_at)} events the model failed to label; "
                  f"left for next run", file=sys.stderr)
    # The watermark must not advance past an event the model failed to label, or the
    # next run's --since window would never re-fetch it (the dispatch_event_has_label
    # check that resumes mid-batch work depends on the event still being in range).
    if unlabeled_created_at:
        watermark_source = min(unlabeled_created_at)
    else:
        watermark_source = max((e["created_at"] for e in events), default=None)
    if watermark_source is not None:
        store.set_state(conn, "dispatch_since", minus(watermark_source, OVERLAP_HOURS), now_iso())
    conn.commit()
    return {"fetched": len(events), "labeled": labeled}


# --- (b) session turns ---------------------------------------------------------------

def pull_devbox_agents_2(since_iso: str, scratch_home: Path) -> None:
    """Mirror pull-da2.sh: copy omp session files devbox-agents-2 modified since
    since_iso into a persistent scratch HOME, so index-sessions.py (run with
    HOME=scratch_home) can index them unchanged. A failed ssh/rsync raises -- this
    stream's whole run fails loud rather than silently skipping devbox-agents-2."""
    dest = scratch_home / ".omp" / "agent" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    file_list = scratch_home / ".da2-files.txt"
    since_date = since_iso[:19].replace("T", " ")
    subprocess.run(
        ["ssh", DEVBOX_AGENTS_2_HOST,
         f"cd ~/.omp/agent/sessions && find . -type f -name '*.jsonl' -newermt '{since_date}' -print0"],
        stdout=file_list.open("wb"), check=True, timeout=120,
    )
    if file_list.stat().st_size == 0:
        return
    subprocess.run(
        ["rsync", "-a", "--from0", f"--files-from={file_list}",
         f"{DEVBOX_AGENTS_2_HOST}:.omp/agent/sessions/", f"{dest}/"],
        check=True, timeout=600,
    )


def index_sessions(home: Path, db_path: Path, all_sessions: bool, days: int) -> None:
    args = ["python3", str(HERE / "index-sessions.py"), "--source", "omp", "--db", str(db_path)]
    args += ["--all"] if all_sessions else ["--days", str(days)]
    subprocess.run(args, check=True, env={**os.environ, "HOME": str(home)})


def extract_turns(db_path: Path, since_iso: str, out_path: Path) -> list[dict]:
    subprocess.run(
        ["python3", str(HERE / "extract-user-messages.py"),
         "--db", str(db_path), "--since", since_iso, "--source", "omp",
         "--allow-empty", "--out", str(out_path)],
        check=True,
    )
    if not out_path.exists():
        return []
    return [json.loads(line) for line in out_path.open()]


def candidate_ids(rec: dict) -> list[tuple[str, str]]:
    """(session, ts) for the record's own send plus every exact-duplicate resend
    (dup_at) -- same logic as experiments-readout.load_turns, so a turn deduped there
    is still one labelled text here, applied to every session/ts it was sent at."""
    return [(rec["session"], rec["ts"])] + [tuple(x) for x in rec.get("dup_at") or []]


async def escalate_to_gemini(todo: list[dict], batch: int = 20, concurrency: int = 8) -> dict[str, str]:
    """Label turns Jev left out with the strong model, batched like
    experiments-readout.label_turns but against standalone_model.completion."""
    labels: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async def run_chunk(chunk):
        prompt = "\n\n".join(f"--- id t{i}\n{t['text']}" for i, t in enumerate(chunk))
        async with sem:
            for _ in range(3):
                h = standalone_model.completion(
                    prompt, system=experiments.TURN_SYSTEM, schema=experiments.TURN_SCHEMA
                )
                try:
                    raw = await asyncio.wait_for(asyncio.to_thread(h.wait), timeout=180)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw)
                got = {r["id"]: r["label"] for r in data["results"]}
                for i, t in enumerate(chunk):
                    if f"t{i}" in got:
                        labels[t["id"]] = got[f"t{i}"]
                if all(t["id"] in labels for t in chunk):
                    return

    await asyncio.gather(*(run_chunk(todo[i:i + batch]) for i in range(0, len(todo), batch)))
    return labels


def label_with_jev(todo: list[dict], scratch: Path) -> dict[str, str]:
    """Run jev-turn-label.py (unchanged, standalone) as a subprocess over `todo`
    ({"id","text"} dicts), returning its confidence>=0.5 labels only -- everything
    else is left for escalate_to_gemini, the same explicit routing SKILL.md step 4
    documents (never a silent fallback)."""
    if not todo:
        return {}
    turns_path = scratch / "turns-for-jev.jsonl"
    with turns_path.open("w") as f:
        for t in todo:
            f.write(json.dumps({"id": t["id"], "text": t["text"]}) + "\n")
    jev_out = scratch / "jev-labels.jsonl"
    env = dict(os.environ)
    env["TYPESAFE_API_KEY"] = os.environ["TYPESAFE_AI_API_KEY"]
    subprocess.run(
        ["uv", "run", "--with", "typesafe-sdk", "python3", str(HERE / "jev-turn-label.py"),
         "--turns", str(turns_path), "--out", str(jev_out)],
        check=True, env=env,
    )
    if not jev_out.exists():
        return {}
    return {json.loads(line)["id"]: json.loads(line)["label"] for line in jev_out.open()}


def run_turns_phase(conn, host: str, db_path: Path, home: Path, scratch: Path) -> dict:
    state_key = f"turns_since_{host}"
    since = store.get_state(conn, state_key)
    if since is None:
        since = minus(now_iso(), TURNS_FIRST_RUN_LOOKBACK_HOURS)

    if host == DEVBOX_AGENTS_2_HOST:
        pull_devbox_agents_2(since, home)
        index_sessions(home, db_path, all_sessions=True, days=0)
    else:
        index_sessions(home, db_path, all_sessions=False, days=3)

    rows = extract_turns(db_path, since, scratch / f"turns-{host}.jsonl")

    # Group by canonical text: a dedup'd resend gets the same label as its canonical
    # send, never a second model call for identical text.
    pending_texts: list[dict] = []  # [{"id": f"text{i}", "text": ...}]
    text_turn_ids: dict[str, list[str]] = {}
    text_min_ts: dict[str, str] = {}
    newest_ts = None
    for rec in rows:
        ids = [f"{sid}|{ts}" for sid, ts in candidate_ids(rec)]
        min_ts = min(ts for _sid, ts in candidate_ids(rec))
        for sid, ts in candidate_ids(rec):
            turn_id = f"{sid}|{ts}"
            store.upsert_turn(conn, turn_id, sid, host, rec.get("project"), ts, rec["chars"])
            if newest_ts is None or ts > newest_ts:
                newest_ts = ts
        already = next((tid for tid in ids if store.turn_has_label(conn, tid)), None)
        if already is not None:
            existing_label, existing_model = store.turn_label_and_model(conn, already)
            for tid in ids:
                if tid != already:
                    store.upsert_turn_label(conn, tid, existing_label, "propagated", now_iso(), existing_model)
            continue
        key = f"text{len(pending_texts)}"
        pending_texts.append({"id": key, "text": rec["text"]})
        text_turn_ids[key] = ids
        text_min_ts[key] = min_ts
    conn.commit()

    jev_labels = label_with_jev(pending_texts, scratch)
    remaining = [t for t in pending_texts if t["id"] not in jev_labels]
    strong_labels = asyncio.run(escalate_to_gemini(remaining)) if remaining else {}

    labeled_texts = 0
    unlabeled_min_ts: list[str] = []
    for key, text_ids in text_turn_ids.items():
        label = jev_labels.get(key)
        source, model = "jev", "typesafe-jev"
        if label is None:
            label = strong_labels.get(key)
            source, model = "strong", standalone_model.GEMINI_MODEL
        if label is None:
            unlabeled_min_ts.append(text_min_ts[key])
            continue  # left for next run, same as classify-sami-events.py's null handling
        for tid in text_ids:
            store.upsert_turn_label(conn, tid, label, source, now_iso(), model)
        labeled_texts += 1
    conn.commit()

    if unlabeled_min_ts:
        print(f"{host} turns: {len(unlabeled_min_ts)} texts the model failed to label; "
              f"left for next run", file=sys.stderr)
    # Same rule as run_dispatch_phase: never advance the watermark past a text the
    # model failed to label, or it falls out of every future --since window.
    watermark_source = min(unlabeled_min_ts) if unlabeled_min_ts else newest_ts
    if watermark_source is not None:
        store.set_state(conn, state_key, minus(watermark_source, OVERLAP_HOURS), now_iso())
        conn.commit()

    return {
        "turns_fetched": sum(len(candidate_ids(r)) for r in rows),
        "texts_pending": len(pending_texts),
        "texts_labeled": labeled_texts,
        "jev": len(jev_labels),
        "strong": len(strong_labels),
    }


# --- CLI -----------------------------------------------------------------------------

def cmd_seed(args) -> None:
    """One-time (re)seed, one series one labeller: the kernel-model opinion already in
    labels.jsonl is archived into dispatch_labels_alt (comparison only, never
    daily_rates), and every one of those 2,352 events is relabelled from scratch with
    this job's own standalone model (standalone-model.py's Gemini, via
    classify.classify_all -- there is no eval-kernel `completion` here, so
    `_completion_fn()` always falls back to it), so the series is one labeller end to
    end from 2026-09-20 rather than switching models at the seed/live boundary."""
    conn = store.init_db(Path(args.db) if args.db else None)
    kernel_model = (
        "anthropic/claude-fable-5-1:xhigh (2026-10-04 weekly reflect run, "
        "omp eval kernel completion, modelRoles.default)"
    )
    seed = store.load_seed_events(args.dispatch_human, args.labels)
    archived = 0
    for _dn, e, kernel_labels in seed:
        store.upsert_dispatch_event(conn, e)
        if kernel_labels is not None:
            store.archive_alt_dispatch_label(conn, e["id"], kernel_model, kernel_labels, now_iso())
            archived += 1
    conn.commit()
    print(f"archived {archived} kernel-model ({kernel_model}) labels into dispatch_labels_alt")

    items = [classify.build_item(e["id"], e) for _dn, e, _kl in seed]
    by_id = asyncio.run(classify.classify_all(items, model="default"))
    relabeled = 0
    for _dn, e, _kl in seed:
        labels = by_id.get(e["id"])
        if labels is None:
            continue
        store.replace_dispatch_label(conn, e["id"], labels, standalone_model.GEMINI_MODEL, now_iso())
        relabeled += 1
    conn.commit()
    print(f"relabeled {relabeled}/{len(seed)} seed events with {standalone_model.GEMINI_MODEL} "
          f"({len(seed) - relabeled} the model failed to label; rerun seed to retry)")

    newest = conn.execute("SELECT MAX(created_at) FROM dispatch_events").fetchone()[0]
    if newest:
        store.set_state(conn, "dispatch_since", minus(newest, OVERLAP_HOURS), now_iso())
    n_rates = store.recompute_daily_rates(conn)
    print(f"daily_rates: {n_rates} rows; dispatch_since={store.get_state(conn, 'dispatch_since')}")


def cmd_run(args) -> None:
    conn = store.init_db(Path(args.db) if args.db else None)
    results = {}
    results["dispatch"] = run_dispatch_phase(conn)
    print(f"dispatch: {results['dispatch']}")

    scratch = DOTFILES_DIR / ".claude"
    results[SAMI_AGENTS_HOST] = run_turns_phase(
        conn, SAMI_AGENTS_HOST, DOTFILES_DIR / ".claude" / "sessions.db", Path.home(), scratch,
    )
    print(f"{SAMI_AGENTS_HOST} turns: {results[SAMI_AGENTS_HOST]}")

    da2_home = scratch / "da2-home"
    results[DEVBOX_AGENTS_2_HOST] = run_turns_phase(
        conn, DEVBOX_AGENTS_2_HOST, scratch / "da2-sessions.db", da2_home, scratch,
    )
    print(f"{DEVBOX_AGENTS_2_HOST} turns: {results[DEVBOX_AGENTS_2_HOST]}")

    n_rates = store.recompute_daily_rates(conn)
    print(f"daily_rates: {n_rates} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="One-time seed of dispatch_events/dispatch_labels")
    seed.add_argument("--dispatch-human", required=True)
    seed.add_argument("--labels", required=True)
    seed.set_defaults(func=cmd_seed)

    run = sub.add_parser("run", help="Daily incremental run (the systemd unit's command)")
    run.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
