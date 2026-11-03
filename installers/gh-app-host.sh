#!/bin/bash
# gh-app-host: on a machine that holds no person's GitHub login, git run outside an agent session
# (an ssh shell, the remote commands of `agentbox move --to`, a unit) gets the GitHub App's token,
# as an agent session's git does.
#
# Sessions and boxes include gh-app-routes.gitconfig; a host shell does not, so its git asks
# ~/.gitconfig's helper, `gh auth git-credential`, for the user's own login. On sami-devbox
# (dispatch://INFRA-993) no person's login may exist, so `git ls-remote` of a private theorem-labs
# repository from an ssh shell failed with "could not read Username" (2026-11-03). An include of
# the routes file here would not help: git reads ~/.config/git/config before ~/.gitconfig, whose
# `helper =` clears it. So this names the profile in the machine's own git config,
# gh-app.host-profile, and shims/gh answers the helper call through `gh-app-token <profile>`. A
# host process has no broker session; gh-app-token registers one under the machine login
# (`secret-run --register`).
#
# Such a machine has no GitHub ssh key either (a key is a person's login), so ssh remotes of
# github.com are fetched and pushed over https, through the same helper.
#
# The machine is told by scripts/on-google-vm: sami-devbox is the Google VM, and the one machine
# whose spec bars a person's login. Anywhere else nothing changes.
set -euo pipefail

if ! "$(dirname "${BASH_SOURCE[0]}")/../scripts/on-google-vm"; then
    echo "gh-app-host: not a Google VM: host git keeps the user's own GitHub login; nothing changed."
    exit 0
fi

PROFILE=agent
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/git/config"
mkdir -p "$(dirname "$CONFIG")"

if [[ "$(git config --file "$CONFIG" --get gh-app.host-profile || true)" != "$PROFILE" ]]; then
    git config --file "$CONFIG" gh-app.host-profile "$PROFILE"
    echo "gh-app-host: $CONFIG gh-app.host-profile -> $PROFILE"
fi
for from in git@github.com: ssh://git@github.com/; do
    if ! git config --file "$CONFIG" --get-all url.https://github.com/.insteadOf | grep -qxF "$from"; then
        git config --file "$CONFIG" --add url.https://github.com/.insteadOf "$from"
        echo "gh-app-host: $CONFIG url $from -> https://github.com/"
    fi
done
