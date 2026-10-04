"""Weekly readout of the omp experiments extension: each feature on versus off.

The extension (omp/extensions/experiments) draws every feature on or off per root session
and records the draw as a `customType: "experiments"` entry in the session file. This joins
each root session's draw to three outcomes, counted from that session's first experiments
entry onward:

- corrections: share of Sami's turns in the session that correct the agent. Each turn is
  labelled by Jev (TypeSafe's System One model) first; only turns Jev answers below
  confidence 0.5 (measured 8.4%) escalate to the strong model through the omp eval
  kernel's `completion()` -- an explicit routing step, never a silent fallback. See
  `jev-turn-label.py` for the measured agreement and the threshold's provenance. Labels
  are cached in `turn_labels_path`, so a rerun pays only for new turns;
- rework: share of the session's merged PRs the delivery dashboard marks as rework;
- cost per PR: the session's model spend (root and every subagent transcript) divided by its
  merged PRs.

The session is the unit: each session contributes one value per outcome it has data for, so a
long-lived session with hundreds of PRs counts once. Each feature is compared marginally (the
draws are independent), as the mean over sessions on minus the mean over sessions off, with a
95% interval from resampling sessions within each arm.

Load it in an eval Python cell, run the Jev pre-pass between two cells (see
`jev-turn-label.py`'s module docstring for the exact commands), then await `main`:

    %load ~/.dotfiles/plugins/sjawhar/skills/reflect/experiments-readout.py
    dump_turns_for_jev(
        session_dirs=["~/.omp/agent/sessions", "<scratch>/.omp/agent/sessions"],
        prompts=["prompts.jsonl", "<other-box>-prompts.jsonl"],
        out_path="turns-for-jev.jsonl",
    )
    # run jev-turn-label.py here, then:
    await main(
        session_dirs=["~/.omp/agent/sessions", "<scratch>/.omp/agent/sessions"],
        prompts=["prompts.jsonl", "<other-box>-prompts.jsonl"],
        turn_labels_path="turn-labels.jsonl",
        jev_labels_path="jev-labels.jsonl",
        out_path="experiments-readout.md",
    )

`prompts` are `extract-user-messages.py --out` files; their suppressed repeats (`dup_at`) are
counted in the session they were sent to.
"""
import asyncio
import gzip
import json
import random
import re
import urllib.request
from pathlib import Path

MARKER = '"customType":"experiments"'
SHIPPED = 1790877600  # 2026-10-01T18:00Z: the extension's first draws; older files carry none
EXCLUDE_DEFAULT = r"^-tmp-(agentc-1508|exp-verify)"
UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

TURN_SYSTEM = """You label turns typed into an AI coding agent's session. Sami is the human
who runs a fleet of coding agents. For each turn decide one label:

correction: Sami tells the agent it did something wrong, misunderstood, made something up, did
  what he did not ask for, should not have done something, or repeats an instruction it should
  already have followed. Rebukes and redirections of the agent's own behavior count.
other: Sami's new request, information, answer, approval, or question that does not say the
  agent erred.
not_sami: text a harness, script, scheduled runner, probe or another agent wrote, not Sami:
  task briefs written by an agent, scheduled ingest prompts, scripted test instructions
  ("Reply with exactly ...", "Use the task tool to spawn exactly ONE subagent ..."), session
  moved or restarted notices, a lone "." nudge.

Return one result per id."""

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": ["correction", "other", "not_sami"]},
                },
                "required": ["id", "label"],
            },
        }
    },
    "required": ["results"],
}


def root_files(session_dir: Path):
    """Yield root session files (one level below the sessions dir) carrying a draw."""
    for proj in sorted(p for p in session_dir.iterdir() if p.is_dir()):
        for f in proj.glob("*.jsonl"):
            if f.stat().st_mtime < SHIPPED:
                continue
            with f.open() as fh:
                if any(MARKER in line for line in fh):
                    yield proj.name, f


def first_draw(path: Path):
    """Timestamp and on/off of every randomized feature in the session's first draw."""
    with path.open() as fh:
        for line in fh:
            if MARKER in line:
                e = json.loads(line)
                if e.get("type") == "custom" and e.get("customType") == "experiments":
                    feats = e["data"]["features"]
                    return e["timestamp"], {
                        k: v["on"] for k, v in feats.items() if v.get("gate") == "random"
                    }
    return None, None


def session_cost(root: Path, since: str) -> float:
    """Model spend in the root transcript and every subagent transcript under it."""
    files = [root] + sorted(root.with_suffix("").rglob("*.jsonl"))
    total = 0.0
    for f in files:
        with f.open() as fh:
            for line in fh:
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                e = json.loads(line)
                if e.get("type") != "message" or e.get("timestamp", "") < since:
                    continue
                cost = ((e.get("message") or {}).get("usage") or {}).get("cost") or {}
                total += cost.get("total") or 0.0
    return total


def load_sessions(session_dirs, exclude):
    """One row per root session id; a session copied between hosts keeps its larger copy."""
    skip = re.compile(exclude)
    best = {}
    for d in session_dirs:
        d = Path(d).expanduser()
        for proj, f in root_files(d):
            if skip.search(proj):
                continue
            m = UUID_RE.search(f.stem)
            sid = m.group(1)
            if sid not in best or f.stat().st_size > best[sid][1].stat().st_size:
                best[sid] = (proj, f)
    sessions = {}
    for sid, (proj, f) in best.items():
        since, arms = first_draw(f)
        if since is None:
            continue
        sessions[sid] = {"project": proj, "file": f, "since": since, "arms": arms}
    return sessions


def load_dataset(url_or_path):
    if str(url_or_path).startswith("http"):
        req = urllib.request.Request(url_or_path, headers={"Accept-Encoding": "gzip"})
        raw = urllib.request.urlopen(req, timeout=120).read()
    else:
        raw = Path(url_or_path).expanduser().read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def load_turns(prompt_paths, sessions):
    """Sami-channel turns per session at or after its first draw, repeats included."""
    turns = []
    for p in prompt_paths:
        for line in Path(p).expanduser().open():
            r = json.loads(line)
            sends = [(r["session"], r["ts"])] + [tuple(x) for x in r.get("dup_at") or []]
            for sess, ts in sends:
                m = UUID_RE.search(sess)
                sid = m.group(1) if m else None
                if sid in sessions and ts >= sessions[sid]["since"]:
                    turns.append({"id": f"{sid}|{ts}", "sid": sid, "text": r["text"][:1500]})
    return list({t["id"]: t for t in turns}.values())


def dump_turns_for_jev(session_dirs, prompts, out_path, exclude=EXCLUDE_DEFAULT):
    """Write every turn `label_turns` would otherwise send to the strong model as
    {"id", "text"} lines, for jev-turn-label.py's external pre-pass to classify. Run
    this, then jev-turn-label.py, before calling `main(..., jev_labels_path=...)` --
    see this module's docstring for the exact commands."""
    sessions = load_sessions(session_dirs, exclude)
    turns = load_turns(prompts, sessions)
    with Path(out_path).expanduser().open("w") as f:
        for t in turns:
            f.write(json.dumps({"id": t["id"], "text": t["text"]}) + "\n")
    return len(turns)



async def label_turns(turns, cache_path, jev_labels_path=None, model="default", batch=20,
                       concurrency=8):
    """Jev-first, strong-model-escalated: jev_labels_path (jev-turn-label.py's --out,
    confidence>=0.5 only -- see its module docstring for the measured agreement) supplies
    a label directly; every other turn still goes to completion() exactly as before. This
    is the confidence-routing step documented in reflect's SKILL.md step 4 -- never a
    silent fallback, since completion() runs on precisely the turns Jev left out."""
    jev_labels = {}
    if jev_labels_path is not None:
        jev_path = Path(jev_labels_path).expanduser()
        if jev_path.exists():
            for line in jev_path.open():
                r = json.loads(line)
                jev_labels[r["id"]] = r["label"]

    cache_path = Path(cache_path).expanduser()
    labels = {}
    if cache_path.exists():
        for line in cache_path.open():
            r = json.loads(line)
            labels[r["id"]] = r["label"]
    todo = [t for t in turns if t["id"] not in labels and t["id"] not in jev_labels]
    sem = asyncio.Semaphore(concurrency)

    async def run(chunk):
        # Short positional ids: the model drops the timestamp half of "<session>|<ts>".
        prompt = "\n\n".join(f"--- id t{i}\n{t['text']}" for i, t in enumerate(chunk))
        async with sem:
            for _ in range(3):
                h = completion(prompt, model=model, system=TURN_SYSTEM, schema=TURN_SCHEMA)
                try:
                    raw = await asyncio.wait_for(asyncio.to_thread(h.wait), timeout=180)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw) if isinstance(raw, str) else raw
                got = {r["id"]: r["label"] for r in data["results"]}
                for i, t in enumerate(chunk):
                    if f"t{i}" in got:
                        labels[t["id"]] = got[f"t{i}"]
                if all(t["id"] in labels for t in chunk):
                    return

    await asyncio.gather(*(run(todo[i:i + batch]) for i in range(0, len(todo), batch)))
    with cache_path.open("w") as fh:
        for k, v in labels.items():
            fh.write(json.dumps({"id": k, "label": v}) + "\n")
    print(f"{len(jev_labels)} turns labelled by Jev, {len(labels)} by the strong model "
          f"({len(todo)} freshly this run)")
    return {**jev_labels, **labels}


def outcomes(sessions, dataset, turns, labels):
    prs_by_sid = {}
    for pr in dataset["prs"]:
        if not pr.get("merged_at"):
            continue
        for sid in pr.get("sessions") or []:
            if sid in sessions and pr["merged_at"] >= sessions[sid]["since"]:
                prs_by_sid.setdefault(sid, []).append(pr)
    by_sid = {}
    for t in turns:
        label = labels.get(t["id"])
        if label in ("correction", "other"):
            c = by_sid.setdefault(t["sid"], [0, 0])
            c[0] += label == "correction"
            c[1] += 1
    unlabelled = sum(1 for t in turns if t["id"] not in labels)
    rows = {}
    for sid, s in sessions.items():
        prs = prs_by_sid.get(sid, [])
        corr = by_sid.get(sid)
        cost = session_cost(s["file"], s["since"])
        rows[sid] = {
            "arms": s["arms"],
            "corrections": corr[0] / corr[1] if corr else None,
            "sami_turns": corr[1] if corr else 0,
            "rework": sum(bool(p.get("rework")) for p in prs) / len(prs) if prs else None,
            "prs": len(prs),
            "cost": cost,
            "cost_per_pr": cost / len(prs) if prs else None,
        }
    return rows, unlabelled


def compare(rows, metric, draws=2000, seed=0):
    rng = random.Random(seed)
    out = {}
    features = sorted({f for r in rows.values() for f in r["arms"]})
    for f in features:
        on = [r[metric] for r in rows.values() if r["arms"].get(f) is True and r[metric] is not None]
        off = [r[metric] for r in rows.values() if r["arms"].get(f) is False and r[metric] is not None]
        if len(on) < 2 or len(off) < 2:
            out[f] = (len(on), len(off), None, None, None)
            continue
        mean = lambda xs: sum(xs) / len(xs)
        diff = mean(on) - mean(off)
        boots = sorted(
            mean(rng.choices(on, k=len(on))) - mean(rng.choices(off, k=len(off)))
            for _ in range(draws)
        )
        out[f] = (len(on), len(off), diff, boots[int(0.025 * draws)], boots[int(0.975 * draws)])
    return out


def render(rows, unlabelled, dataset_at, n_turns):
    fmt = {"corrections": "{:+.3f}", "rework": "{:+.3f}", "cost_per_pr": "{:+.2f}"}
    names = {
        "corrections": "Share of Sami's turns that correct the agent",
        "rework": "Share of the session's merged PRs marked rework",
        "cost_per_pr": "Model spend per merged PR, USD",
    }
    lines = [
        "# Experiments readout",
        "",
        f"Root sessions with a draw: {len(rows)}. With Sami turns: "
        f"{sum(r['sami_turns'] > 0 for r in rows.values())}. With merged PRs: "
        f"{sum(r['prs'] > 0 for r in rows.values())}. Delivery dataset generated "
        f"{dataset_at}. Turns read: {n_turns}, unlabelled: {unlabelled}.",
        "",
        "Difference is mean over sessions with the feature on minus mean with it off; the "
        "interval is a 95% bootstrap over sessions. An interval that includes 0 shows no "
        "detectable effect.",
    ]
    for metric, title in names.items():
        lines += ["", f"## {title}", "", "| feature | sessions on | sessions off | difference | 95% interval |", "|---|---|---|---|---|"]
        for f, (n_on, n_off, d, lo, hi) in compare(rows, metric).items():
            if d is None:
                lines.append(f"| {f} | {n_on} | {n_off} | too few sessions | |")
            else:
                lines.append(
                    f"| {f} | {n_on} | {n_off} | {fmt[metric].format(d)} | "
                    f"[{fmt[metric].format(lo)}, {fmt[metric].format(hi)}] |"
                )
    return "\n".join(lines) + "\n"


async def main(session_dirs, prompts, turn_labels_path, out_path,
               dataset="http://127.0.0.1:4317/api/dataset", exclude=EXCLUDE_DEFAULT,
               model="default", jev_labels_path=None):
    sessions = load_sessions(session_dirs, exclude)
    data = load_dataset(dataset)
    turns = load_turns(prompts, sessions)
    labels = await label_turns(turns, turn_labels_path, jev_labels_path=jev_labels_path,
                                model=model)
    rows, unlabelled = outcomes(sessions, data, turns, labels)
    text = render(rows, unlabelled, data.get("generated_at"), len(turns))
    Path(out_path).expanduser().write_text(text)
    print(text)
    return rows
