# scripts

## Tests

- Run a script's tests with pytest, never as `python3 scripts/tests/<file>`: a file with no `unittest.main()` then runs no test and can still exit 0, and no file run that way gets the test term list `conftest.py` gives.

## Adding or changing a script

- A secret never goes in a command's arguments: any user can read them from `/proc/<pid>/cmdline`. Pass it through the environment, a file or stdin (curl's `--config` or `-H @file`, `mcp-remote-from-env` for a URL).
- Test for a Google VM with `on-google-vm`, not a copy of its check.
- A script that resumes an omp session runs `omp-session-live <id>` first and refuses while it exits 0.
- Text that is data (a message body, a prompt) goes in a file passed by path (`--message-file`, `--body-file`). Inline, a shell runs its backticks, and `ssh` and `systemd-run` split it again. Where a tool has only an inline form, pass one `/bin/sh -c` whose whole command is one quoted argument, then read back what it stored (a unit's `ExecStart`).
- Find a process by its exact argv or `/proc/<pid>/exe`: `pgrep -f <pattern>` also matches the shell or `ssh` command that runs the search.

## Acting on the system

- Check the state you act on before you act. An installer leaves a unit that is disabled and inactive alone and says so, since someone stopped it on purpose; a sweep confirms each thing it removes.
- Read a value before you set a new one (a memory or pids cap, a limit) and print `old → new`. If new is smaller, stop and ask the owner whether they meant it: `docker update --memory` shrinks as readily as it grows.
- A permission denial is a fact about the caller. Before it becomes a claim about the resource, ask again as another identity, or read what the code grants.
- A census counts paths as they are and groups only in what it prints: a path normalised before counting measures its parent.
- Judge state an earlier run saved under this run's mode: a `--dry-run` must not act on a pending entry that a live run wrote.

## Scheduled scripts

- Do not add a timer or service that monitors a dev box: the standard Datadog or Cloud agent does that. The scripts and units of the retired watches are still here, and `devbox/expected-timers.txt` names them: do not arm them again.
- A unit has no login shell: name each tool by its full path, or set `PATH` in the unit.
- `sudo` resets the environment: pass a variable as `sudo -n env VAR=value cmd`.
- A watch says each new fact once over Envoy, in at most 600 characters that name a report file.
- A watch for a state nobody can enumerate is a follower, not a periodic pass. Its docs say its silence is a result, so a quiet period is no reason to remove it.
- A green timer shows the unit ran, not that it did its job. Before you rely on a watch, make it fire once on a harmless case routed to you.

Where this file and the code disagree, the code is right: follow the code and correct this file.
