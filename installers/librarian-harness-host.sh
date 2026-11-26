#!/bin/bash
# What the librarian's test harnesses (dispatch://INFRA-602's EvalSkillStaging jail and the
# INFRA-805 harness) need on the host and mise.toml does not pin. Standalone installer, not sourced
# by install.sh; sami-devbox-provision runs it in step `install`, and it is safe to run again.
#
#   1. omp 18.13.3-sami.20261029-115123, installed beside the daily pin and not made active: the
#      harnesses run that exact build (lib.sh OMP_BIN, run.sh OMP_BIN), or a round's arms are not
#      comparable. mise keeps replaced versions (upgrade.auto_prune=false in mise.toml). The install
#      holds $MISE_DATA_DIR/.dotfiles-install.lock, the lock oryx/install.sh's op step,
#      dotfiles-follow, dotfiles-sync, agent-session, shims/knives and installers/mise.sh take
#      around their `mise install`, and waits at most 600 s for it. Its mise gets
#      DOTFILES_INSTALL_LOCK_HELD=1, so a knives call under it that lacks its pin exits 127 at once
#      instead of waiting on the lock. A caller that set the marker holds the lock already, so under
#      it the install takes no lock.
#   2. Playwright's Chromium for the D2 scoring check (`uv run --no-project --with playwright`), in
#      the browsers directory .bashrc names. The libraries it loads are devbox/gce-host.sh's
#      "Chrome libraries". devbox/nvme-cache-cap empties that directory when it passes its cap;
#      run this installer again then.
#
# The rest of the harnesses' list is elsewhere: bun 1.4.4 and uv in mise.toml; bwrap, socat and the
# Postgres 16 server binaries in devbox/gce-host.sh; linger in gce-host.sh; tq, bq and the gateway
# token through install.sh and devbox/install.sh.
set -euo pipefail

HARNESS_OMP="18.13.3-sami.20261029-115123"

export PATH="$HOME/.dotfiles/bin:$HOME/.mise/shims:$PATH"
export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"
# Absolute, as scripts/dotfiles-follow makes it, before the cd below: the install goes there, and
# its .dotfiles-install.lock is the one the dotfiles' other installs take.
[[ "$MISE_DATA_DIR" = /* ]] || MISE_DATA_DIR="$PWD/$MISE_DATA_DIR"
user="${USER:-$(id -un)}"
if [ -d "/mnt/nvme/$user" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/mnt/nvme/$user/cache/ms-playwright}"
fi
export PLAYWRIGHT_SKIP_BROWSER_GC=1

# From $HOME, so mise resolves uv by the global config, not a checkout's own (or untrusted) mise.toml.
cd "$HOME"
# .dotfiles-install.lock and DOTFILES_INSTALL_LOCK_HELD: see item 1 at the top of this file.
if [ -n "${DOTFILES_INSTALL_LOCK_HELD:-}" ]; then
    mise install "github:sjawhar/oh-my-pi@$HARNESS_OMP"
else
    lock="$MISE_DATA_DIR/.dotfiles-install.lock"
    mkdir -p "$MISE_DATA_DIR"
    rc=0
    # mise's own 75 becomes 1 under the lock, so 75 means only that the 600 s wait ran out.
    # shellcheck disable=SC2016  # the inner bash expands $1, $? and $rc; this shell must not
    DOTFILES_INSTALL_LOCK_HELD=1 flock -w 600 -E 75 "$lock" \
        bash -c 'rc=0; mise install "$1" || rc=$?; [ "$rc" != 75 ] || rc=1; exit "$rc"' bash \
        "github:sjawhar/oh-my-pi@$HARNESS_OMP" || rc=$?
    if [ "$rc" = 75 ]; then
        echo "librarian-harness-host: another install held $lock for 600 s; omp $HARNESS_OMP not installed" >&2
    fi
    [ "$rc" = 0 ] || exit "$rc"
fi
omp_bin="$MISE_DATA_DIR/installs/github-sjawhar-oh-my-pi/$HARNESS_OMP/bin/omp"
[ -x "$omp_bin" ] || { echo "librarian-harness-host: no $omp_bin after mise install" >&2; exit 1; }
echo "librarian-harness-host: omp $HARNESS_OMP at $omp_bin"

uv run --no-project --with playwright playwright install chromium
uv run --no-project --with playwright python - << 'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    print("librarian-harness-host: Playwright Chromium", b.version, "launches")
    b.close()
PY
