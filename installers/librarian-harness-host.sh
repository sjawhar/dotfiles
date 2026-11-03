#!/bin/bash
# What the librarian's test harnesses (dispatch://INFRA-602's EvalSkillStaging jail and the
# INFRA-805 harness) need on the host and mise.toml does not pin. Standalone installer, not sourced
# by install.sh; sami-devbox-provision runs it in step `install`, and it is safe to run again.
#
#   1. omp 18.13.3-sami.20261029-115123, installed beside the daily pin and not made active: the
#      harnesses run that exact build (lib.sh OMP_BIN, run.sh OMP_BIN), or a round's arms are not
#      comparable. mise keeps replaced versions (upgrade.auto_prune=false in mise.toml).
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
user="${USER:-$(id -un)}"
if [ -d "/mnt/nvme/$user" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/mnt/nvme/$user/cache/ms-playwright}"
fi
export PLAYWRIGHT_SKIP_BROWSER_GC=1

# From $HOME, so mise resolves uv by the global config, not a checkout's own (or untrusted) mise.toml.
cd "$HOME"
mise install "github:sjawhar/oh-my-pi@$HARNESS_OMP"
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
