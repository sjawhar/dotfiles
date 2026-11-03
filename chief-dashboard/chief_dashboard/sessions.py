"""How many agent sessions run on this machine now, and in which stream's tmux window: measured from /proc, tmux and
docker on every read, never declared by a lead (Sami, 2026-11-02: no cap, but each stream's session count beside its
number, "so growth is visible and you intervene when it looks wrong").

A live agent session is a top-level `omp` process: one whose argv[0] is `omp` (any directory) or whose command line
names the oh-my-pi install, with no such process among its ancestors. omp's own helpers run the same binary as
`omp __omp_worker_<name>` (the daemon broker, the embedding server, a JS eval process) and are not sessions, even
when their own session has exited and left them orphaned.

Each session is attributed by where it runs:

- In a tmux pane: the pane's window, when the pane belongs to the agents tmux session (`AGENTS_TMUX`). A pane in
  any other tmux session is ungrouped and named by that tmux session.
- In an agent box (`scripts/agentbox`): the box's omp runs under docker-init in the container, with no pane. Its
  container comes from the process's cgroup (`docker-<id>.scope`), its name from `docker ps`, and the host side is
  the `docker run --name <box>` process, whose pane gives the window (`w-<box>` today).
- Neither: ungrouped, "no tmux pane".

A window of the agents session that is not in `WINDOW_STREAMS` is ungrouped, named by the window: a stream whose
lanes run outside its window is what this count exists to show."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The tmux windows on sami-rocqperf and the stream issue each one holds: dispatch://INFRA-202/spec, section
# "Windows on sami-rocqperf" (the stream issues are its "Streams" table). A new window is one line here. A window
# mapped to None is known but has no stream row on the page: `chief` holds the chief of staff and the agents in no
# stream, and `cicd` is not in the stream table. Every other window is ungrouped.
WINDOW_STREAMS: dict[str, str | None] = {
    "chief": None,
    "ossv": "OSSV-142",
    "linux": "LINUX-31",
    "tasks": "BET-83",
    "grader": "BET-59",
    "infra": "INFRA-203",
    "rocq": "INFRA-167",
    "cicd": None,
}
# The tmux session (or session group) that holds the windows above. A pane in another tmux session is ungrouped.
AGENTS_TMUX = "agents"

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CONTAINER_CGROUP = re.compile(r"docker-([0-9a-f]{64})\.scope")
SESSION_ARG = re.compile(rf"(?:^|_)({UUID})(?:\.jsonl)?$")
# omp's pane title: "π", then a spinner frame (a braille character) or ">" when idle, then the agent's own title.
TITLE_PREFIX = re.compile(r"^π\s*[\u2800-\u28ff>]?\s*")
# omp's pane title last: it is free text and may hold a tab, so it takes what the split leaves.
PANE_FORMAT = "\t".join(
    ["#{pane_id}", "#{pane_pid}", "#{session_name}", "#{session_group}", "#{window_name}", "#{pane_start_command}",
     "#{pane_title}"]
)
# What `agentbox move --to` respawns a moved session's old pane with (scripts/agentbox, move_to_member): a shell
# that says where the session went. The old pane stays in its window on the machine the session left, so it is the
# one record of which window, and so which stream, a moved session came from.
MOVED_PANE = re.compile(rf"agentbox: session ({UUID}) moved to \S+ at ")


# ------------------------------------------------------------------------------------------------- reading


def read_processes(proc: Path = Path("/proc")) -> list[dict]:
    """Every process: pid, ppid, uid, argv, and the cgroup of the ones that look like omp (the only ones whose
    cgroup is read). A process that exits during the read is skipped."""
    out = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            raw = (d / "cmdline").read_bytes()
            stat = (d / "stat").read_text()
            uid = d.stat().st_uid
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        # The command name is parenthesised and may hold spaces and parentheses, so the fields follow the LAST ")".
        fields = stat[stat.rindex(")") + 2 :].split()
        p = {"pid": int(d.name), "ppid": int(fields[1]), "uid": uid, "argv": argv, "cgroup": ""}
        if is_omp(p):
            try:
                p["cgroup"] = (d / "cgroup").read_text().strip()
            except OSError:
                pass
        out.append(p)
    return out


def read_panes() -> list[dict]:
    """Every tmux pane of the default server. Raises OSError or CalledProcessError when tmux cannot be read: a
    service reaches the server only with the TMUX_TMPDIR its shell would have (the unit sets it)."""
    done = subprocess.run(["tmux", "list-panes", "-a", "-F", PANE_FORMAT], capture_output=True, text=True, timeout=20,
                          check=True)
    panes = []
    for line in done.stdout.splitlines():
        parts = line.split("\t", 6)
        if len(parts) != 7:
            continue
        pane, pid, name, group, window, start, title = parts
        panes.append({"pane": pane, "pid": int(pid), "tmux": group or name, "window": window, "start": start,
                      "title": title})
    return panes


def read_containers() -> dict[str, str]:
    """Running containers, full id to name (`docker ps`, as `agentbox move --plan` reads them)."""
    done = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}}\t{{.Names}}"], capture_output=True,
                          text=True, timeout=20, check=True)
    return dict(line.split("\t", 1) for line in done.stdout.splitlines() if "\t" in line)


def read(errors: list[str]) -> dict | None:
    """One measurement, as the snapshot keeps it (see `measure`); None when the processes or tmux cannot be read,
    since counts without panes would put every session outside its window."""
    try:
        processes = read_processes()
    except OSError as e:
        errors.append(f"agent sessions: /proc: {e}")
        return None
    try:
        panes = read_panes()
    except (OSError, subprocess.SubprocessError) as e:
        errors.append(f"agent sessions: tmux list-panes: {getattr(e, 'stderr', '') or e}".strip())
        return None
    try:
        containers = read_containers()
    except (OSError, subprocess.SubprocessError) as e:
        errors.append(f"agent sessions: docker ps (agent boxes are counted without their window): "
                      f"{getattr(e, 'stderr', '') or e}".strip())
        containers = {}
    return measure(processes, panes, containers)


def record(processes: list[dict], panes: list[dict], containers: dict[str, str]) -> dict:
    """The raw reading as a test fixture: only the processes a measurement reads (the omp processes, each box's
    `docker run`, every ancestor of either, the panes' own processes), each command line cut to what the measurement
    reads: no environment, mounts or paths beyond a program's own name. Refuses a fixture that measures differently
    from the reading it was cut from."""
    by_pid = {p["pid"]: p for p in processes}
    keep: set[int] = set()
    for p in processes:
        if is_omp(p) or box_name(p):
            q: dict | None = p
            while q is not None and q["pid"] not in keep:
                keep.add(q["pid"])
                q = by_pid.get(q["ppid"])
    keep |= {pn["pid"] for pn in panes if pn["pid"] in by_pid}
    home = str(Path.home())

    def scrub(p: dict) -> dict:
        argv = p["argv"]
        if box_name(p):
            cut = [argv[0], "run", "--name", box_name(p)]
        elif argv and argv[0].endswith("containerd-shim-runc-v2") and "-id" in argv[:-1]:
            cut = [argv[0], "-id", argv[argv.index("-id") + 1]]
        else:  # flags, worker names and session ids: never a prompt, a path or an environment value
            cut = argv[:1] + [a.rsplit("/", 1)[-1] for a in argv[1:] if "=" not in a and (
                a.startswith("-") or a.startswith("__omp_worker_") or SESSION_ARG.search(a))]
        return {**p, "argv": [a.replace(home, "~") for a in cut]}

    # A pane's start command can hold a prompt; the measurement never reads it.
    cut = [{k: v for k, v in pn.items() if k != "start"}
           for pn in panes if pn["pid"] in keep or pn["tmux"] == AGENTS_TMUX]
    raw = {"processes": [scrub(p) for p in processes if p["pid"] in keep], "panes": cut, "containers": containers}
    if measure(raw["processes"], cut, containers, "") != measure(processes, panes, containers, ""):
        raise ValueError("the scrubbed reading measures differently from the live one")
    return raw


# ------------------------------------------------------------------------------------------------- measuring


def is_omp(p: dict) -> bool:
    argv = p["argv"]
    return bool(argv) and (os.path.basename(argv[0]) == "omp" or any("oh-my-pi" in a for a in argv))


def is_worker(p: dict) -> bool:
    return any(a.startswith("__omp_worker_") for a in p["argv"][1:2])


def box_name(p: dict) -> str:
    """The container name of a `docker run --name NAME ...` process, else ""."""
    argv = p["argv"]
    if not argv or os.path.basename(argv[0]) != "docker" or "run" not in argv[1:2]:
        return ""
    for i, a in enumerate(argv):
        if a == "--name" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--name="):
            return a.split("=", 1)[1]
    return ""


def session_id(argv: list[str]) -> str:
    """The session id named on the command line (`--resume <id>` or a transcript `..._<id>.jsonl`), else ""."""
    for a in argv[1:]:
        m = SESSION_ARG.search(a)
        if m:
            return m.group(1)
    return ""


def clean_title(title: str, host: str = "") -> str:
    """A pane title without omp's prefix and spinner; "" when it is only the host name (no title set)."""
    t = TITLE_PREFIX.sub("", title).strip()
    return "" if t == host else t


@dataclass
class Session:
    pid: int
    session: str  # the session id its command line names, else ""
    title: str  # its pane's title (the host pane's, for a box), cleaned; "" when none
    box: str = ""  # the agent box's container name, else ""
    window: str = ""  # tmux window name, "" when it has no pane
    tmux: str = ""  # tmux session (group) name, "" when it has no pane
    headless: bool = False  # an `omp -p` run (a review gate, a scheduled step), not an interactive session


@dataclass
class Place:
    kind: str  # "window", "tmux" (a pane outside the agents session), "box" (a box with no host pane) or "none"
    name: str
    sessions: list[Session] = field(default_factory=list)


def measure(processes: list[dict], panes: list[dict], containers: dict[str, str], host: str | None = None) -> dict:
    """{total, windows: {window: [session]} for every window of WINDOW_STREAMS, ungrouped: [place], idle_windows}:
    plain data, as the snapshot keeps it. `idle_windows` are windows of the agents session outside WINDOW_STREAMS
    that hold no session."""
    host = socket.gethostname() if host is None else host
    by_pid = {p["pid"]: p for p in processes}
    pane_of: dict[int, dict] = {}
    for pn in panes:  # a pane of a grouped session is listed once per session of the group: one is enough
        pane_of.setdefault(pn["pid"], pn)
    runs = {box_name(p): p for p in processes if box_name(p)}

    def ancestors(p: dict):
        seen: set[int] = set()
        q: dict | None = p
        while q is not None and q["pid"] not in seen:
            seen.add(q["pid"])
            yield q
            q = by_pid.get(q["ppid"])

    def pane_above(p: dict) -> dict | None:
        return next((pane_of[q["pid"]] for q in ancestors(p) if q["pid"] in pane_of), None)

    def container_of(p: dict) -> str:
        m = CONTAINER_CGROUP.search(p.get("cgroup") or "")
        if m:
            return m.group(1)
        for q in ancestors(p):  # the box's runtime shim, when the cgroup was unreadable
            if q["argv"] and q["argv"][0].endswith("containerd-shim-runc-v2") and "-id" in q["argv"]:
                i = q["argv"].index("-id")
                return q["argv"][i + 1] if i + 1 < len(q["argv"]) else ""
        return ""

    def top_level(p: dict) -> bool:
        return is_omp(p) and not is_worker(p) and not any(is_omp(q) for q in list(ancestors(p))[1:])

    windows: dict[str, list[Session]] = {w: [] for w in WINDOW_STREAMS}
    places: dict[tuple[str, str], Place] = {}
    total = 0
    for p in sorted(processes, key=lambda p: p["pid"]):
        if not top_level(p):
            continue
        total += 1
        s = Session(p["pid"], session_id(p["argv"]), "", headless=bool({"-p", "--print"} & set(p["argv"][1:])))
        pane = pane_above(p)
        if pane is None:
            cid = container_of(p)
            if cid:
                s.box = containers.get(cid) or f"container {cid[:12]}"
                run = runs.get(s.box)
                pane = pane_above(run) if run else None
        if pane is not None:
            s.title, s.window, s.tmux = clean_title(pane["title"], host), pane["window"], pane["tmux"]
        if pane is not None and pane["tmux"] == AGENTS_TMUX and pane["window"] in windows:
            windows[pane["window"]].append(s)
            continue
        if pane is not None and pane["tmux"] == AGENTS_TMUX:
            key = ("window", pane["window"])
        elif pane is not None:
            key = ("tmux", pane["tmux"])
        elif s.box:
            key = ("box", s.box)
        else:
            key = ("none", "")
        places.setdefault(key, Place(*key)).sessions.append(s)
    used = {name for kind, name in places if kind == "window"}
    idle = sorted({pn["window"] for pn in panes if pn["tmux"] == AGENTS_TMUX}
                  - set(WINDOW_STREAMS) - used)
    order = {"window": 0, "tmux": 1, "box": 2, "none": 3}
    ungrouped = sorted(places.values(), key=lambda pl: (order[pl.kind], pl.name))
    return {
        "total": total,
        "windows": {w: [asdict(s) for s in ss] for w, ss in windows.items()},
        "ungrouped": [asdict(pl) for pl in ungrouped],
        "idle_windows": idle,
    }


def stream_counts(census: dict | None) -> dict[str, int]:
    """Live sessions per stream issue, summed over the windows WINDOW_STREAMS gives that issue."""
    out: dict[str, int] = {}
    for window, sessions in ((census or {}).get("windows") or {}).items():
        issue = WINDOW_STREAMS.get(window)
        if issue:
            out[issue] = out.get(issue, 0) + len(sessions)
    return out


def stream_windows(issue: str) -> list[str]:
    return [w for w, i in WINDOW_STREAMS.items() if i == issue]


def moved_from(panes: list[dict]) -> dict[str, str]:
    """{session id: window} for each session moved off this machine from a window of the agents session, read from
    the shell `agentbox move --to` left in its old pane (MOVED_PANE)."""
    out: dict[str, str] = {}
    for pn in panes:
        m = MOVED_PANE.search(pn.get("start") or "")
        if m and pn["tmux"] == AGENTS_TMUX:
            out[m.group(1)] = pn["window"]
    return out


def ungrouped_count(census: dict | None) -> int:
    return sum(len(pl["sessions"]) for pl in (census or {}).get("ungrouped") or [])


def place_words(place: dict) -> str:
    """Where an ungrouped place is, in words: "window w-curl-asan", "tmux session gm-1189", ..."""
    return {"window": "window {}", "tmux": "tmux session {}", "box": "agent box {}, no tmux pane",
            "none": "no tmux pane"}[place["kind"]].format(place["name"])


def label(s: dict) -> str:
    """A session as a person names it: its pane's title, else what it is."""
    if s.get("title"):
        return s["title"]
    if s.get("headless"):
        return "headless run (omp -p)"
    return f"session {s['session']}" if s.get("session") else f"pid {s['pid']}"


def report(census: dict) -> str:
    """The census as text, for `chief-dashboard sessions`."""
    lines = [f"{census['total']} live agent sessions"]
    for window, sessions in census["windows"].items():
        issue = WINDOW_STREAMS.get(window)
        lines.append(f"  {window:<8} {len(sessions):>3}  {issue or '(no stream row)'}")
    lines.append(f"ungrouped: {ungrouped_count(census)}")
    for pl in census["ungrouped"]:
        for s in pl["sessions"]:
            box = f" [box {s['box']}]" if s["box"] else ""
            lines.append(f"  {place_words(pl)}: pid {s['pid']} {label(s)}{box}")
    if census["idle_windows"]:
        lines.append("windows with no agent: " + ", ".join(census["idle_windows"]))
    return "\n".join(lines)
