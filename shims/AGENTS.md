# shims

- Before you add a side effect or a lookup to a shim, find every other wrapper of the same
  binary and every caller that already does what you add: a lock can collide with theirs, and a
  lookup can find a wrapper that hands the call back to the shim, which then loops.
- A test that runs a shim, jj or an editor starts it with `LeakTestCase.launch`, in a cgroup with
  a task limit and a timeout, so a shim that finds itself fails the case instead of forking
  without end on a machine other sessions share. Most shim tests call `subprocess.run`; do not
  copy that in a new one.
- A shim test runs each case with `run_without_tty` or `run_with_tty`. A person's case drops
  `SSH_CONNECTION`; one that commits brings its own HOME, git config and author.

Where this file and the code disagree, the code is right: follow the code and correct this file.
