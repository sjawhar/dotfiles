#!/bin/bash
set -euo pipefail

# COSMIC desktop settings: laptop/cosmic/ is laid out like ~/.config/cosmic.
# COSMIC saves a setting by writing a temp file and renaming it over the key
# (cosmic-config's atomicwrites), which turns a linked key into a plain file
# but leaves a linked directory in place. So each component's directory is the
# link, and a setting changed in COSMIC lands in laptop/cosmic/ as a change to
# commit or revert. CosmicComp's directory also holds compositor state
# (pinned_workspaces), so only its keyboard key is linked; saving that key
# turns the link into a plain file, which ensure_link relinks while the value
# still matches.
mkdir -p ~/.config/cosmic
for component in CosmicTheme.Mode CosmicAppletTime CosmicNotifications CosmicIdle CosmicAudio; do
    ensure_link "${LAPTOP_DIR}/cosmic/com.system76.${component}" ~/.config/cosmic/"com.system76.${component}"
done
mkdir -p ~/.config/cosmic/com.system76.CosmicComp/v1
ensure_link "${LAPTOP_DIR}/cosmic/com.system76.CosmicComp/v1/xkb_config" \
    ~/.config/cosmic/com.system76.CosmicComp/v1/xkb_config
