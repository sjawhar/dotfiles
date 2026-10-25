# What already prunes on its own

- `~/.dotfiles/agentbox/devbox-prune` runs every 6 h where its timer is on. On the host daemon it does Docker step 6 ([docker.md](docker.md)). In each box it does steps 2 and 6, a default-builder `docker builder prune -f`, and a cut of Go build cache idle >2 days. It never uses `-a`, deletes a tagged image or judges liveness. In a box, read its log with `systemd-run --user systemctl --user status devbox-prune.service`.
- agentbox deletes each session's workspace when omp exits.

Neither covers the rest, which this skill does by hand: unused tagged images, non-default builders, leaked leases, containers, box directories filled before agentbox began deleting each session's workspace on exit, Docker volumes, `~/.local/share/opencode`, a retired jj store.
