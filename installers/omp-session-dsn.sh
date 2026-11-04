#!/bin/bash
# Link, but do not arm, the two units a dev-box session needs to write its transcripts to Postgres
# (dispatch://INFRA-1192): the Cloud SQL Auth Proxy to agents-postgres on 127.0.0.1:5432, and the
# timer that keeps the DSN for omp_sessions in tmpfs (scripts/omp-session-dsn).
#
# Standalone installer — not sourced by install.sh, and it enables nothing itself. On sami-devbox
# both units were armed by hand at 2026-11-04 20:57Z, once the database, the login and the proxy
# were proved (dispatch://INFRA-1192): the DSN sits at $XDG_RUNTIME_DIR/omp-session-sql/dsn and
# psql logs in through it as the machine's IAM database user, with no password anywhere. Run this
# again after a change to a unit, then restart what changed. The last lines print the arming
# commands, for a machine where they are not armed.
#
# Run it on sami-devbox. The machine's Application Default Credentials must be a service account
# with roles/cloudsql.client and roles/cloudsql.instanceUser on theorem-agent-infra and an IAM
# database user on agents-postgres that may write omp_sessions (agent-infra postgres/, config
# boxWriterAccounts). On sami-devbox that account is dev-sjawhar@theorem-agent-infra, which the
# metadata server gives both the host and every box.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "$(hostname -s)" in
    sami-devbox) ;;
    *)
        echo "omp-session-dsn: only sami-devbox writes omp_sessions this way today; not installing here" >&2
        exit 1
        ;;
esac
[ -x "$HOME/.local/bin/cloud-sql-proxy" ] || {
    echo "omp-session-dsn: no ~/.local/bin/cloud-sql-proxy, which the proxy unit runs." >&2
    echo "  agent-infra omp-session-sql/install.sh installs it, pinned and checked against its published sha256." >&2
    exit 1
}

mkdir -p "$HOME/.config/systemd/user"
for unit in cloud-sql-proxy-agents-postgres.service omp-session-dsn.service omp-session-dsn.timer; do
    ensure_link "$HOME/.dotfiles/omp/$unit" "$HOME/.config/systemd/user/$unit"
done
systemctl --user daemon-reload

cat << 'EOF'
omp-session-dsn: units linked. This script enables nothing. To arm them on a machine where they
are not armed yet:

  systemctl --user enable --now cloud-sql-proxy-agents-postgres.service
  systemctl --user enable --now omp-session-dsn.timer
  systemctl --user start omp-session-dsn.service

Then prove the login, which needs no password:

  psql "$(cat "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/omp-session-sql/dsn")" -Atc 'select current_user, current_database()'

A box started after that reaches the same DSN at the same path and the proxy at the same address
(scripts/agentbox). A box started before it does not: make a new one.
EOF
