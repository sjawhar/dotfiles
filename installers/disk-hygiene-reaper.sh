#!/bin/bash
# Hourly PLAN-ONLY workspace-reaper timer (AGENTC-79 D7). The service runs
# reaper_plan.py, which has no apply path; enabling the timer schedules
# evidence-gathering only. Wired from devbox/install.sh (shared agent boxes),
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
