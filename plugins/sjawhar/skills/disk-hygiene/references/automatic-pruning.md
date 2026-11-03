# What already prunes on its own

Read this to learn what timers already prune and what they leave for this skill, before you add automation of your own. SKILL.md's rule, as it stood with its reason:

- Never put this skill on a timer or cron: it needs the check-in. Run it by hand, and stop when done. What already runs on a timer, and what it leaves for this skill: this file.

- `~/.dotfiles/agentbox/devbox-prune` runs every 6 h where its timer is on. On the host daemon it does Docker step 6 ([docker.md](docker.md)). In each box it does steps 2 and 6, a default-builder `docker builder prune -f`, and a cut of Go build cache idle >2 days. It never uses `-a`, deletes a tagged image or judges liveness. In a box, read its log with `systemd-run --user systemctl --user status devbox-prune.service`.
- agentbox deletes each session's workspace when omp exits.
- `~/.dotfiles/devbox/tmp-empty-prune.timer` (armed by `devbox/install.sh`) runs daily and deletes the empty `tmp*` directories older than a day directly under `/mnt/nvme/$USER/tmp`, which Python's `tempfile.mkdtemp()` leaves behind.
- `~/.dotfiles/devbox/gocache-cap.timer` (armed by `devbox/install.sh`) runs hourly on the box's one Go build cache, `/mnt/nvme/$USER/gocache` (`GOCACHE` in `.bashrc`), and on any lane cache inside it. Past 25 GB it deletes the entries no build used in the last 3 hours; if the directory is still past 50 GB, it runs `go clean -cache`. Go itself trims only entries unused for 5 days. A `GOCACHE` outside that directory is not capped.
- `~/.dotfiles/devbox/tmp-stale-reap.timer` (armed by `devbox/install.sh`) runs hourly and removes a top-level entry of the root-resident `/tmp` once nothing in it has been written for 48 h. It never touches an entry another user owns, an entry any process holds (cwd, exe, root, an open fd, a mapped file, `TMPDIR` or `AGENT_SESSION_SCRATCH`), or a tree holding a unix socket or another user's file at any depth; `devbox/tmp-stale-reap --dry-run` prints the same decisions and removes nothing. `/tmp` is on the root filesystem and `systemd-tmpfiles`' 30-day rule never reaches it. A pass stops itself at 28 min (`stopped at its deadline … next pass resumes at …` in its journal) and the next hourly pass carries on from that entry.

None of these covers the rest, which this skill does by hand: unused tagged images, non-default builders, leaked leases, containers, box directories filled before agentbox began deleting each session's workspace on exit, Docker volumes, `~/.local/share/opencode`, a retired jj store.
