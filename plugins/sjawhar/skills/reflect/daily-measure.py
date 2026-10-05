#!/usr/bin/env python3
"""Daily incremental version of the reflect skill's two label corpora: extend
classify-sami-events.py's Dispatch-event labels and session-turn labels by whatever is
new since the last successful run, store everything in reflect-store.py's SQLite
store, and recompute daily_rates. Read by daily-readout.py and by the weekly reflect
run (SKILL.md step 2), so a fix's effect on a failure class shows within days instead
of only at the next weekly sitting.

One series, one labeller: Claude (`standalone-model.py`, `claude-fable-5-1` at `xhigh`
effort, the same model behind the 2026-10-04 weekly baseline's `modelRoles.default`)
labels both corpora. Jev (`jev-turn-label.py`, TypeSafe System One) also labels every
turn, but only as a comparison opinion in `turn_labels_alt` (with its own confidence),
never the series -- see `reflect-store.py`'s module docstring for the schema this
enforces.

Runs unattended under omp/reflect-daily.service (installers/reflect-daily.sh): no eval
kernel, so every model call is direct HTTP. The Dispatch and turn series calls go
through `standalone-model.py`, which reaches Claude over the Hawk middleman gateway
(`providers.anthropic.baseUrl` in `~/.omp/agent/models.yml`) authenticated with a fresh
`hawk-token-fast` read per request -- no secret grant needed for this path, since the
gateway itself holds the credential. The Jev comparison pass uses the TypeSafe SDK
(`jev-turn-label.py`), authenticated through `secret-run TYPESAFE_AI_API_KEY -- ...`
(the agent-secrets broker for a registered session, secretsd's agent tier otherwise).

Resumability: each of the three streams (Dispatch events, sami-agents turns,
devbox-agents-2 turns) tracks its own `job_state` watermark and only advances it after
that stream's series labelling + commit fully succeeds, so a mid-run failure (an
unreachable gateway, a dead ssh host, a Dispatch API outage) leaves every
already-succeeded stream's progress intact and only the failed one is retried from the
same point next run. Every insert is keyed by a stable id (the Dispatch event's own id;
`session|ts` for a turn) via `INSERT OR IGNORE`, so re-fetching an overlapping window
is a no-op, never a duplicate row or a re-spent model call. Any stream's failure makes
this process exit non-zero -- never caught and continued -- so the systemd unit goes
`failed` and the journal shows exactly which stream and why. The one deliberate
exception: a turn-phase's Jev comparison pass failing (a TypeSafe outage, a missing
TYPESAFE_AI_API_KEY) is caught, reported, and still makes the run exit non-zero at the
very end, but only after that phase's Claude series labels are committed and its
watermark advanced -- a Jev outage costs that day's `turn_labels_alt` comparison row,
never the series.
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
    model_id = standalone_model.MODEL_ID
    new_events = [e for e in events
                  if not store.dispatch_event_has_label(conn, e["id"])
                  and not store.is_refused(conn, "dispatch", e["id"], model_id)]
    labeled = 0
    refused: set = set()
    unlabeled_created_at: list[str] = []
    if new_events:
        items = [classify.build_item(e["id"], e) for e in new_events]
        by_id = asyncio.run(classify.classify_all(items, model="default", refused=refused))
        for e in new_events:
            if e["id"] in refused:
                store.upsert_dispatch_event(conn, e)
                store.record_refusal(conn, "dispatch", e["id"], model_id, now_iso())
                continue
            labels = by_id.get(e["id"])
            if labels is None:
                unlabeled_created_at.append(e["created_at"])
                continue
            store.upsert_dispatch_event(conn, e)
            store.upsert_dispatch_label(conn, e["id"], labels, model_id, now_iso())
            labeled += 1
        if refused:
            print(f"dispatch: {len(refused)} events {model_id} refused to label; recorded in "
                  f"refusals and left out of the series", file=sys.stderr)
        if unlabeled_created_at:
            print(f"dispatch: {len(unlabeled_created_at)} events the model failed to label; "
                  f"left for next run", file=sys.stderr)
    # The watermark must not advance past an event that failed transiently, or the next
    # run's --since window would never re-fetch it. A refused event is recorded and does
    # not hold it: retrying a content refusal never succeeds.
    if unlabeled_created_at:
        watermark_source = min(unlabeled_created_at)
    else:
        watermark_source = max((e["created_at"] for e in events), default=None)
    if watermark_source is not None:
        store.set_state(conn, "dispatch_since", minus(watermark_source, OVERLAP_HOURS), now_iso())
    conn.commit()
    return {"fetched": len(events), "labeled": labeled, "refused": len(refused)}


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


async def label_with_claude(todo: list[dict], batch: int = 20, concurrency: int = 8,
                            refused: set | None = None) -> dict[str, str]:
    """Label every pending turn text with the series labeller (standalone-model.py's
    Claude), batched like experiments-readout.label_turns. A chunk with texts left over
    is split until each leftover is tried on its own, so one text the model refuses
    never costs the rest of its chunk. When `refused` is a set, the id of every text
    refused on its own on every attempt (`stop_reason: "refusal"`, no text block) is
    added to it; any other unlabelled text (timeouts, malformed replies) is simply absent
    from the result, for the caller to retry on a later run."""
    labels: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async def label_chunk(chunk):
        if not chunk:
            return
        prompt = "\n\n".join(f"--- id t{i}\n{t['text']}" for i, t in enumerate(chunk))
        chunk_labels: dict[str, str] = {}
        attempts = refusals = 0
        for _ in range(3):
            attempts += 1
            async with sem:
                h = standalone_model.completion(
                    prompt, system=experiments.TURN_SYSTEM, schema=experiments.TURN_SCHEMA
                )
                try:
                    raw = await asyncio.wait_for(asyncio.to_thread(h.wait), timeout=180)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    if type(e).__name__ != "Refusal":
                        raise
                    refusals += 1
                    continue
            try:
                data = json.loads(raw)
                got = {r["id"]: r["label"] for r in data["results"]}
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            for i, t in enumerate(chunk):
                if f"t{i}" in got:
                    chunk_labels[t["id"]] = got[f"t{i}"]
            if len(chunk_labels) == len(chunk):
                break
        labels.update(chunk_labels)
        if len(chunk) == 1:
            if not chunk_labels and refusals == attempts and refused is not None:
                refused.add(chunk[0]["id"])
            return
        remaining = [t for t in chunk if t["id"] not in chunk_labels]
        if remaining:
            mid = max(1, len(remaining) // 2)
            await label_chunk(remaining[:mid])
            await label_chunk(remaining[mid:])

    await asyncio.gather(*(label_chunk(todo[i:i + batch]) for i in range(0, len(todo), batch)))
    return labels


def label_with_jev(todo: list[dict], scratch: Path) -> dict[str, tuple[str, float]]:
    """Run jev-turn-label.py as a subprocess over `todo` ({"id","text"} dicts),
    returning every turn it answers with its confidence (jev-turn-label.py's
    --out-all) -- comparison only (turn_labels_alt), never the series. `turns_path`/
    `jev_out` are overwritten fresh every call: `todo`'s ids (`text0`, `text1`, ...)
    are scoped to this run alone, so a stale file from an earlier day would make
    jev-turn-label.py's own resume logic wrongly treat today's different turns as
    already answered."""
    if not todo:
        return {}
    turns_path = scratch / "turns-for-jev.jsonl"
    with turns_path.open("w") as f:
        for t in todo:
            f.write(json.dumps({"id": t["id"], "text": t["text"]}) + "\n")
    jev_out = scratch / "jev-labels-all.jsonl"
    jev_out.unlink(missing_ok=True)
    env = dict(os.environ)
    env["TYPESAFE_API_KEY"] = os.environ["TYPESAFE_AI_API_KEY"]
    subprocess.run(
        ["uv", "run", "--with", "typesafe-sdk", "python3", str(HERE / "jev-turn-label.py"),
         "--turns", str(turns_path), "--out-all", str(jev_out)],
        check=True, env=env,
    )
    if not jev_out.exists():
        return {}
    out = {}
    for line in jev_out.open():
        r = json.loads(line)
        out[r["id"]] = (r["label"], r["confidence"])
    return out


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
        if any(store.is_refused(conn, "turn", tid, standalone_model.MODEL_ID) for tid in ids):
            continue
        key = f"text{len(pending_texts)}"
        pending_texts.append({"id": key, "text": rec["text"]})
        text_turn_ids[key] = ids
        text_min_ts[key] = min_ts
    conn.commit()

    # Series first: every pending text goes to Claude, and the result is committed
    # (including the watermark) before Jev is even invoked, so a Jev outage below can
    # never hold up today's series.
    refused: set = set()
    claude_labels = (asyncio.run(label_with_claude(pending_texts, refused=refused))
                     if pending_texts else {})

    labeled_texts = 0
    unlabeled_min_ts: list[str] = []
    for key, text_ids in text_turn_ids.items():
        if key in refused:
            for tid in text_ids:
                store.record_refusal(conn, "turn", tid, standalone_model.MODEL_ID, now_iso())
            continue
        label = claude_labels.get(key)
        if label is None:
            unlabeled_min_ts.append(text_min_ts[key])
            continue  # left for next run, same as classify-sami-events.py's null handling
        for tid in text_ids:
            store.upsert_turn_label(conn, tid, label, "claude", now_iso(), standalone_model.MODEL_ID)
        labeled_texts += 1
    conn.commit()

    if refused:
        print(f"{host} turns: {len(refused)} texts {standalone_model.MODEL_ID} refused to label; "
              f"recorded in refusals and left out of the series", file=sys.stderr)
    if unlabeled_min_ts:
        print(f"{host} turns: {len(unlabeled_min_ts)} texts the model failed to label; "
              f"left for next run", file=sys.stderr)
    # Same rule as run_dispatch_phase: never advance the watermark past a text that failed
    # transiently; a refused text is recorded and does not hold it.
    watermark_source = min(unlabeled_min_ts) if unlabeled_min_ts else newest_ts
    if watermark_source is not None:
        store.set_state(conn, state_key, minus(watermark_source, OVERLAP_HOURS), now_iso())
        conn.commit()

    # Jev: comparison only, archived into turn_labels_alt with its own confidence,
    # never read by daily_rates. Runs over the same pending texts the series just
    # committed; a failure here (caught, not re-raised) must never cost the series or
    # the watermark above, which are already durable by this point -- cmd_run fails the
    # whole process at the very end instead, after every phase's series work is done.
    jev_answered = 0
    jev_error: str | None = None
    if pending_texts:
        try:
            jev_labels = label_with_jev(pending_texts, scratch)
        except Exception as exc:
            jev_labels = {}
            jev_error = f"{type(exc).__name__}: {exc}"
        for key, (label, confidence) in jev_labels.items():
            for tid in text_turn_ids[key]:
                store.archive_turn_label_alt(conn, tid, store.JEV_MODEL, label, confidence, now_iso())
        conn.commit()
        jev_answered = len(jev_labels)
        if jev_error:
            print(f"{host} turns: Jev comparison pass failed (series already committed): "
                  f"{jev_error}", file=sys.stderr)

    return {
        "turns_fetched": sum(len(candidate_ids(r)) for r in rows),
        "texts_pending": len(pending_texts),
        "texts_labeled": labeled_texts,
        "jev_answered": jev_answered,
        "jev_error": jev_error,
    }


# --- CLI -----------------------------------------------------------------------------

def cmd_seed(args) -> None:
    """One-time (re)seed, one series one labeller: the kernel-model opinion already in
    labels.jsonl is archived into dispatch_labels_alt (comparison only, never
    daily_rates), and every one of those 2,352 events is relabelled from scratch with
    this job's own standalone model (standalone-model.py's Claude, via
    classify.classify_all -- there is no eval-kernel `completion` here, so
    `_completion_fn()` always falls back to it), so the series is one labeller end to
    end from 2026-09-20 rather than switching models at the seed/live boundary. Only
    needed for a brand-new store (job_state has no `dispatch_since` watermark yet);
    use `reseed` instead to migrate an already-running store to a new labeller."""
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
    refused: set = set()
    by_id = asyncio.run(classify.classify_all(items, model="default", refused=refused))
    relabeled = 0
    for _dn, e, _kl in seed:
        if e["id"] in refused:
            store.record_refusal(conn, "dispatch", e["id"], standalone_model.MODEL_ID, now_iso())
            continue
        labels = by_id.get(e["id"])
        if labels is None:
            continue
        store.replace_dispatch_label(conn, e["id"], labels, standalone_model.MODEL_ID, now_iso())
        relabeled += 1
    conn.commit()
    print(f"relabeled {relabeled}/{len(seed)} seed events with {standalone_model.MODEL_ID}; "
          f"refused {len(refused)}, failed {len(seed) - relabeled - len(refused)}")

    newest = conn.execute("SELECT MAX(created_at) FROM dispatch_events").fetchone()[0]
    if newest:
        store.set_state(conn, "dispatch_since", minus(newest, OVERLAP_HOURS), now_iso())
    n_rates = store.recompute_daily_rates(conn)
    print(f"daily_rates: {n_rates} rows; dispatch_since={store.get_state(conn, 'dispatch_since')}")


EPOCH = "1970-01-01T00:00:00.000Z"


def reseed_dispatch(conn) -> dict:
    """Move every dispatch_labels row from a labeller other than the standing one into
    dispatch_labels_alt (tagged with its own model) and out of the series, then refetch
    every Sami dispatch event and label, with the standing Claude labeller, each one that
    has neither its label nor its refusal yet. Resumable: a rerun after transient
    failures labels only what is still missing. Payload isn't persisted in
    dispatch_events (only event metadata is), so this refetches live from Dispatch --
    the same fetch `run_dispatch_phase` uses, from the beginning of time."""
    model_id = standalone_model.MODEL_ID
    archived = 0
    for event_id, labels_json, model, labeled_at in conn.execute(
        "SELECT event_id, labels, model, labeled_at FROM dispatch_labels WHERE model != ?", (model_id,)
    ).fetchall():
        store.archive_alt_dispatch_label(conn, event_id, model, json.loads(labels_json), labeled_at)
        archived += 1
    conn.execute("DELETE FROM dispatch_labels WHERE model != ?", (model_id,))
    conn.commit()
    print(f"moved {archived} dispatch_labels rows from other labellers into dispatch_labels_alt")

    events = fetch_sami_events_since(EPOCH)
    for e in events:
        store.upsert_dispatch_event(conn, e)
    conn.commit()

    todo = [e for e in events
            if not store.dispatch_event_has_label(conn, e["id"])
            and not store.is_refused(conn, "dispatch", e["id"], model_id)]
    refused: set = set()
    items = [classify.build_item(e["id"], e) for e in todo]
    by_id = asyncio.run(classify.classify_all(items, model="default", refused=refused)) if items else {}
    relabeled = 0
    for e in todo:
        if e["id"] in refused:
            store.record_refusal(conn, "dispatch", e["id"], model_id, now_iso())
            continue
        labels = by_id.get(e["id"])
        if labels is None:
            continue
        store.replace_dispatch_label(conn, e["id"], labels, model_id, now_iso())
        relabeled += 1
    conn.commit()
    failed = len(todo) - relabeled - len(refused)
    print(f"dispatch reseed: {len(events)} events, {len(todo)} to label; labelled {relabeled}, "
          f"refused {len(refused)}, failed {failed}" + ("; rerun reseed to retry" if failed else ""))

    newest = conn.execute("SELECT MAX(created_at) FROM dispatch_events").fetchone()[0]
    if newest:
        store.set_state(conn, "dispatch_since", minus(newest, OVERLAP_HOURS), now_iso())
        conn.commit()
    return {"events": len(events), "archived": archived, "relabeled": relabeled,
            "refused": len(refused), "failed": failed}


def reseed_turns(conn, scratch: Path, da2_home: Path) -> dict:
    """Move every turn_labels row from a labeller other than the standing one into
    turn_labels_alt, tagged `(pre-reseed ... label, archived without confidence)` so it
    never collides with the live `typesafe-jev` identity on turn_labels_alt's
    (turn_id, model) key, and out of the series. `turns` stores no text (only metadata),
    so every known turn id is re-extracted fresh per host (`all_sessions=True`: a
    reseed must not miss a turn older than the daily job's lookback window); each turn
    with neither a Claude label nor a Claude refusal is labelled, and each turn without
    a Jev comparison row gets one with its confidence. Resumable: a rerun after
    transient failures labels only what is still missing."""
    model_id = standalone_model.MODEL_ID
    archived = 0
    for turn_id, label, source, model, labeled_at in conn.execute(
        "SELECT turn_id, label, source, model, labeled_at FROM turn_labels WHERE model IS NOT ?",
        (model_id,),
    ).fetchall():
        tag = f"{model} (pre-reseed {source} label, archived without confidence)"
        store.archive_turn_label_alt(conn, turn_id, tag, label, None, labeled_at)
        archived += 1
    conn.execute("DELETE FROM turn_labels WHERE model IS NOT ?", (model_id,))
    conn.commit()
    print(f"moved {archived} turn_labels rows from other labellers into turn_labels_alt")

    by_host: dict[str, list[tuple[str, str]]] = {}
    for turn_id, host, ts in conn.execute("SELECT id, host, ts FROM turns").fetchall():
        by_host.setdefault(host, []).append((turn_id, ts))

    text_by_turn_id: dict[str, str] = {}
    for host, host_rows in by_host.items():
        since = minus(min(ts for _tid, ts in host_rows), OVERLAP_HOURS)
        if host == DEVBOX_AGENTS_2_HOST:
            pull_devbox_agents_2(since, da2_home)
            db_path = scratch / "da2-sessions.db"
            index_sessions(da2_home, db_path, all_sessions=True, days=0)
        else:
            db_path = DOTFILES_DIR / ".claude" / "sessions.db"
            index_sessions(Path.home(), db_path, all_sessions=True, days=0)
        rows = extract_turns(db_path, since, scratch / f"reseed-turns-{host}.jsonl")
        for rec in rows:
            for sid, ts in candidate_ids(rec):
                text_by_turn_id[f"{sid}|{ts}"] = rec["text"]

    known_ids = [tid for rows in by_host.values() for tid, _ts in rows]
    with_text: list[dict] = []
    missing = []
    for turn_id in known_ids:
        text = text_by_turn_id.get(turn_id)
        if text is None:
            missing.append(turn_id)
            continue
        with_text.append({"id": turn_id, "text": text})
    if missing:
        print(f"turns reseed: {len(missing)}/{len(known_ids)} known turn ids have no text in a "
              f"fresh extraction (session file pruned/moved?); left unlabelled", file=sys.stderr)

    pending = [t for t in with_text
               if not store.turn_has_label(conn, t["id"])
               and not store.is_refused(conn, "turn", t["id"], model_id)]
    refused: set = set()
    claude_labels = asyncio.run(label_with_claude(pending, refused=refused)) if pending else {}
    relabeled = 0
    for turn_id, label in claude_labels.items():
        store.replace_turn_label(conn, turn_id, label, "claude", now_iso(), model_id)
        relabeled += 1
    for turn_id in refused:
        store.record_refusal(conn, "turn", turn_id, model_id, now_iso())
    conn.commit()
    failed = len(pending) - relabeled - len(refused)
    print(f"turns reseed: {len(with_text)} turns with text, {len(pending)} to label; labelled "
          f"{relabeled}, refused {len(refused)}, failed {failed}"
          + ("; rerun reseed to retry" if failed else ""))

    has_jev = {r[0] for r in conn.execute(
        "SELECT turn_id FROM turn_labels_alt WHERE model = ?", (store.JEV_MODEL,))}
    jev_todo = [t for t in with_text if t["id"] not in has_jev]
    jev_answered = 0
    jev_error: str | None = None
    if jev_todo:
        try:
            jev_labels = label_with_jev(jev_todo, scratch)
        except Exception as exc:
            jev_labels = {}
            jev_error = f"{type(exc).__name__}: {exc}"
        for turn_id, (label, confidence) in jev_labels.items():
            store.archive_turn_label_alt(conn, turn_id, store.JEV_MODEL, label, confidence, now_iso())
        conn.commit()
        jev_answered = len(jev_labels)
        if jev_error:
            print(f"turns reseed: Jev comparison pass failed: {jev_error}", file=sys.stderr)

    return {
        "archived": archived, "known": len(known_ids), "missing_text": len(missing),
        "relabeled": relabeled, "refused": len(refused), "failed": failed,
        "jev_answered": jev_answered, "jev_error": jev_error,
    }


def cmd_reseed(args) -> None:
    """Migrate the store to a single Claude series: move every dispatch_labels and
    turn_labels row from another labeller into the _alt tables, label every stored
    dispatch event and turn that has neither a Claude label nor a Claude refusal, and
    give every turn a Jev comparison row with its confidence. Resumable: a rerun labels
    only what is still missing. Exits non-zero while anything failed transiently, so the
    run is repeated until every item is labelled or refused."""
    conn = store.init_db(Path(args.db) if args.db else None)
    dispatch_result = reseed_dispatch(conn)
    print(f"dispatch reseed: {dispatch_result}")

    scratch = DOTFILES_DIR / ".claude"
    da2_home = scratch / "da2-home"
    turns_result = reseed_turns(conn, scratch, da2_home)
    print(f"turns reseed: {turns_result}")

    n_rates = store.recompute_daily_rates(conn)
    print(f"daily_rates: {n_rates} rows")

    failed = dispatch_result["failed"] + turns_result["failed"]
    if failed or turns_result.get("jev_error"):
        raise SystemExit(
            f"reseed incomplete: {dispatch_result['failed']} dispatch events and "
            f"{turns_result['failed']} turns failed transiently"
            + (f"; Jev comparison pass failed ({turns_result['jev_error']})"
               if turns_result.get("jev_error") else "")
            + ". Everything labelled so far is committed; rerun reseed to finish."
        )


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

    # The Claude series (both hosts) and daily_rates above are already committed by
    # this point; a Jev comparison-pass outage must still fail the unit (so a human
    # sees it), but only after everything the series needs is durable -- never before.
    jev_errors = {
        host: results[host]["jev_error"] for host in (SAMI_AGENTS_HOST, DEVBOX_AGENTS_2_HOST)
        if results[host].get("jev_error")
    }
    if jev_errors:
        raise SystemExit(
            f"Jev comparison labelling failed for {len(jev_errors)} host(s) "
            f"({jev_errors}); the Claude series and daily_rates are committed "
            "regardless, but this run still fails so the unit surfaces the outage -- "
            "that day's turns simply have no turn_labels_alt comparison row"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="One-time seed of a brand-new dispatch_events/dispatch_labels")
    seed.add_argument("--dispatch-human", required=True)
    seed.add_argument("--labels", required=True)
    seed.set_defaults(func=cmd_seed)

    reseed = sub.add_parser(
        "reseed",
        help="One-time full migration of an already-running store to a new series "
        "labeller: archives every stored dispatch/turn label into the _alt tables "
        "and relabels everything from scratch (see module docstring).",
    )
    reseed.set_defaults(func=cmd_reseed)

    run = sub.add_parser("run", help="Daily incremental run (the systemd unit's command)")
    run.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
