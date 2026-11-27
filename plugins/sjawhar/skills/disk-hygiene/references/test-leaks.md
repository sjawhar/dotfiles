# Leaks from tests and processes

Read this when processes a test or harness left behind cause the space or load, or when you write such a test. `S` is set in SKILL.md. Find the leak before you guess at it: `references/finding-space.md` has the instruments (`cpu`, `writers`, `fdwrites`) that find the process and the file it grows.

- A test that forks a detached server must reap it when the parent dies.
  - A tool timeout is a SIGKILL that skips `finally`, so use a watchdog that polls the test's pid.
- Run each mutation-test mutant under a wall-clock `timeout`: an unguarded mutant can loop forever or fill the disk.
- Run each mutation-test mutant in a process group the harness kills on exit, for the same reason.
- Run each mutation-test mutant with output capped (`ulimit -f`), for the same reason.
- Read `ps -o stat=` before you trust a kill: a stopped process (`T` state) accepts `kill -TERM` with exit 0 and stays alive until `SIGCONT`.
- After you kill a build, look for survivors by what they run, not by the pids you killed: `pgrep -af <your work-dir path>`, then `ps -o pid,ppid,etime` on the hits.
  - A `PPID` of 1 and an elapsed time older than your kill is a reparented worker: a pool (loky, multiprocessing, `xargs -P`, `make -j`) outlives a parent that dies without reaping it.
  - Kill the survivors' process group (`kill -TERM -<pgid>`).
  - After you kill the process group, check by path.
- When the process is not yours, prefer `SIGSTOP` to a kill: it stops the writing at once, loses nothing, and leaves its owner to resume or end it.
- A pid is not a program: `exec` keeps the pid and replaces what runs.
  - If you hold a reference to a running thing, check its identity again where you act (`stat /proc/<pid>/fd/<fd>`, compare dev and ino with what you recorded), not only where you recorded it.
- A watcher that may kill takes the exact pid of the one job it guards.
- A watcher that may kill kills only that pid and its process group.
- A watcher that may kill exits when that pid exits.
- When you abandon an attempt, kill its watcher by its recorded pid.
- After you kill an abandoned attempt's watcher, check that it is gone.
- Never stop or kill by a name pattern.
- Decide that work has ended by the work itself: its process and its container are both gone and its log has an end line. Never by a wrapper's pid or exit code.
