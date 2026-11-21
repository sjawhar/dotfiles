# agentbox

- Test a change from your clone, on the host: `DOTFILES_DIR=<clone> <clone>/scripts/agentbox doctor` (or `new`). Without `DOTFILES_DIR` the launcher builds the image from, and reads `mounts` and the shims of, `~/.dotfiles`, the live checkout, so the box shows nothing of your change.
- Tests, from the repository root: `uvx pytest scripts/tests/test_agentbox_*.py scripts/tests/test_box_ssh_config.py`, and `bun test scripts/agentbox-run.test.ts`.
- Remove a path every box gets (a `mounts` entry, or a bind `scripts/agentbox` gives every box) only on purpose, and announce it: a box writes to a path it was not given in its own filesystem, with no error, and loses the writes at exit.

Where this file and the code disagree, the code is right: follow the code and correct this file.
