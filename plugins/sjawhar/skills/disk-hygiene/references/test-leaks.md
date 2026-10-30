# Leaks from tests and processes

Read this when the space or load comes from processes a test or harness left behind, or when you write such a test. `S` is as set in SKILL.md.

- Find the leak before you guess at it. `python3 $S cpu --seconds 5` groups CPU by command name, which is where a fan-out of one name hides. `sudo python3 $S writers --seconds 5` names the processes that write now, with their working directories. `python3 $S fdwrites --seconds 5 --pid <pid>` names the file one of them grows.
- A test that forks a detached server must reap it when the parent dies. A tool timeout is a SIGKILL that skips `finally`, so use a watchdog that polls the test's pid.
- Run each mutation-test mutant under a wall-clock `timeout`, in a process group that the harness kills on exit, with output capped (`ulimit -f`). A mutant without a guard can loop forever or fill the disk.
- A stopped process (`T` state) accepts `kill -TERM` with rc 0 and stays alive until `SIGCONT`. Read `ps -o stat=` before you trust a kill.
