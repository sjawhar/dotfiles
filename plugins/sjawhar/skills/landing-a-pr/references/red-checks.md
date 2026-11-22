# A red check

Read before you act on a red check.

- Read a red check's log before you act on it.
- Call a test flaky only when you can name the mechanism.
- Reproduce a suspected regression.
- Fix real defects.
- Fix a test that fails at random at its cause.
- Do not rerun, quarantine or narrow lanes past a test that fails at random, or ask the owner which tests to run, since the skipped flake fails the next PR.
- Rerun only a failure outside every test (a lost runner, a failed download), once, as failed jobs on a run of the current head.
