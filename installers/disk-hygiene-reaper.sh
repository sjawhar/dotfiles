#!/bin/bash
# Hourly workspace-reaper timer (AGENTC-79 D7, amended 2026-09-18). The service
# plans every hour and, when free space is below `apply_below_free_gb`, consumes
# that plan through the same verified apply path a hand-run uses. Above the floor
# it is still evidence-gathering only. Set the key to 0 for the original
# plan-only behaviour. Wired from devbox/install.sh (shared agent boxes),
# not from the main install.sh.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKILL_DIR="${DOTFILES_DIR}/plugins/sjawhar/skills/disk-hygiene"
units=(disk-hygiene-reaper.service disk-hygiene-reaper-failure.service disk-hygiene-reaper.timer)

mkdir -p "${HOME}/.config/systemd/user" "${HOME}/.config/disk-hygiene"
for unit in "${units[@]}"; do
    ensure_link "${SKILL_DIR}/systemd/${unit}" "${HOME}/.config/systemd/user/${unit}"
done

# Per-machine config, seeded once and never overwritten: absolute paths stay out
# of the committed tree, and operators extend the fixed protected set in place.
config="${HOME}/.config/disk-hygiene/reaper.json"
if [ ! -f "$config" ]; then
    cat > "$config" <<EOF
{
 "repo": "${HOME}/agent-c",
 "roots": ["${HOME}/.worktrees", "/tmp"],
 "fresh_hours": 24,
 "apply_below_free_gb": 600,
 "protected": ["${HOME}/agent-c", "${HOME}/.dotfiles"],
 "anchors": []
}
EOF
    echo "Seeded ${config} — review the repo/roots/protected entries for this machine."
fi

systemctl --user daemon-reload 2>/dev/null \
    || echo "NOTE: could not reload systemd --user (no user session here?) — reload on the target machine."
systemctl --user enable --now disk-hygiene-reaper.timer 2>/dev/null \
    || echo "NOTE: could not enable disk-hygiene-reaper.timer — enable it on the target machine."
