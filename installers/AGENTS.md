# installers

Per-tool install scripts. The root `install.sh` sources the ones it lists, in order, and runs `agent-secrets-local.sh` as a command; an installer it does not list does not run from it. A machine's own script (`devbox/install.sh`, `laptop/install.sh`, `oryx/install.sh`, and `devbox/gce-host.sh` for the root-level `sysbox.sh` and `tailscale.sh`) runs the role-specific installers. An installer's header says what it does and what it refuses.

## Writing an installer

- Start with `#!/bin/bash`, `set -euo pipefail` and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"`.
- Use the `lib.sh` helpers, each documented there, instead of a hand-made link, clone, JSON edit, `.deb` install or unit: `ensure_link`, `ensure_clone`, `ensure_vendor`, `ensure_command`, `ensure_json`, `install_checked_deb`, `arm_user_timer`, `arm_user_service`, `ensure_box_health_owner`.
- A second run changes nothing.
- A step for one machine role goes in a role installer that the role's install script runs, never in the root `install.sh`: the role must be explicit.
- Before an installer changes a unit or system state, it checks that state first (`scripts/AGENTS.md`, "Acting on the system").

## Running and testing

- `bash install.sh` runs everything the root lists; `bash installers/<name>.sh [args]` runs one. A configuration link applies at the tool's next start; a binary is on `PATH` at once.
- Tests are in `scripts/tests/`, named for the installer or the helper: `uvx pytest scripts/tests/test_ensure_link.py`.
