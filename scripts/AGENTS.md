# scripts

Utility commands, the watches that run as systemd user units, and their tests.
`.bashrc` puts this directory on `PATH` (after `shims/` and `bin/`), so a script runs by name with no install step.
Each script's usage and rules are in its header comment or `--help`; a script with more rules than a header holds has `<name>.md` beside it.

## Tests

- Tests for a script are `scripts/tests/test_<name>*.py`, dashes in the name written as underscores.
- Run them with pytest from the repository root: `conftest.py` gives them the shims' encrypted test term list, and some files have no `unittest.main()`.
- One file: `uvx pytest scripts/tests/test_envoy_client.py`. Every file: `uvx pytest scripts/tests`.
- Stand-ins: `scripts/tests/fake_gh.py` for `gh pr create|view|merge`, `scripts/tests/gce_metadata_stub.py` for the Google metadata server (point `GCE_METADATA_HOST` at it).
- Give a script an environment override for each system path it reads (`/proc`, a state or run directory), so a test runs it against a fixture tree, as `omp-session-live` (`OMP_SESSION_LIVE_PROC`) and `work-dir` (`WORK_DIR_ROOT`) do.

## Adding or changing a script

- Read the sibling scripts first and match their naming and interpreter. An entrypoint has a `#!` line and the executable bit.
- A library has neither: `envoy_client.py`, the Envoy client the Python watches import from their own directory, and `agent-spend-env.sh`, which is sourced. A shell, or a script that sends one message and exits, uses `scripts/envoy`.
- An edit saved in `~/.dotfiles` is live for every session and unit at once; test from a copy (root `AGENTS.md`).
- A secret never goes in a command's arguments: any user can read them from `/proc/<pid>/cmdline`. Pass it through the environment, a file or stdin (curl's `--config` or `-H @file`, `mcp-remote-from-env` for a URL).
- A script that needs a secret runs `secret-run KEY… -- cmd`, not `secrets` or `agent-secrets`.
- Test for a Google VM with `on-google-vm`, not a copy of its check.
- A script that resumes an omp session runs `omp-session-live <id>` first and refuses while it exits 0.
- Text that is data (a message body, a prompt) goes in a file passed by path (`--message-file`, `--body-file`). Inline, a shell runs its backticks, and `ssh` and `systemd-run` split it again.
- Find a process by its exact argv or `/proc/<pid>/exe`: `pgrep -f <pattern>` also matches the shell or `ssh` command that runs the search.

## Acting on the system

- Check the state you act on before you act. An installer leaves a unit that is disabled and inactive alone and says so, since someone stopped it on purpose; a sweep confirms each thing it removes.
- Judge state an earlier run saved under this run's mode: a `--dry-run` must not act on a pending entry that a live run wrote.

## Scheduled scripts

- Units are `omp/<name>.{service,timer}` or `devbox/<name>.{service,timer}` with `ExecStart=%h/.dotfiles/scripts/<name>`. An installer (`installers/<name>.sh`, or `devbox/install.sh` for the dev box's own) arms them with `arm_user_timer` or `arm_user_service` from `installers/lib.sh`.
- A timer the dev box must run gets a line in `devbox/expected-timers.txt`; `unit-health-watch` reports one that is missing or stops firing.
- Do not add a timer or service that monitors a dev box: the standard Datadog or Cloud agent does that. The scripts and units of the retired watches are still here; `devbox/expected-timers.txt` names them. Do not arm them again.
- A unit has no login shell: name each tool by its full path, or set `PATH` in the unit.
- `sudo` resets the environment: pass a variable as `sudo -n env VAR=value cmd`.
- A watch says each new fact once over Envoy, in at most 600 characters that name a report file.
- A green timer shows the unit ran, not that it did its job. Before you rely on a watch, make it fire once on a harmless case routed to you.

## Other guides

- `agentbox/AGENTS.md`: `agentbox`, `agentbox-*` and their tests.
- `shims/AGENTS.md`: the scripts a shim runs (`leak-terms-check`, `person-branch-guard`, `docs-pr-gate`, `gh-approved-post`, `omp-session-trailer`, `github-repo-visibility`, `gh-routed-only`, `public-text-gate`, `pr-gate`, `gh-read-cache`).
- `omp/AGENTS.md`, "models.yml": `omp-models-build`.
- `agent-secrets/AGENTS.md`: `agent-secrets-login`, `agent-secrets-session`, `agent-secrets-name-session`.
- Root `AGENTS.md`: `agent-session`, `cld`, and publishing with `dotfiles-publish`. `dotfiles-follow.md`: how a machine follows main.
- `installers/AGENTS.md`: the installer helpers.
