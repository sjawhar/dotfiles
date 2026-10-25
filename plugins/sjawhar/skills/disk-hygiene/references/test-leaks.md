# Leaks from tests and processes

Read this when the space or load comes from processes a test or harness left behind, or when you write such a test.

- A test that forks a detached server must reap it when the parent dies. A tool timeout is a SIGKILL that skips `finally`, so use a watchdog that polls the test's pid.
- Run each mutation-test mutant under a wall-clock `timeout`, in a process group that the harness kills on exit, with output capped (`ulimit -f`). A mutant without a guard can loop forever or fill the disk.
- A stopped process (`T` state) accepts `kill -TERM` with rc 0 and stays alive until `SIGCONT`. Read `ps -o stat=` before you trust a kill.
