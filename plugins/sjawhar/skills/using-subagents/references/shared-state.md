# What your subagents share with you

## Environment

- Treat `*_PROJECT_ENVIRONMENT`, `PYTHONPATH`, `VIRTUAL_ENV`, `DOCKER_CONFIG` and every other ambient pointer as untrusted. Before you trust a measurement, print the path the tool resolved and check that it is inside your workspace.
- A brief for a lane that reproduces CI names CI's environment as an instruction ("run it once with the region unset"): CI has fewer variables and tools than a dev box.
- Every subagent's bash calls run in the parent's one persistent shell, but each call starts in the session cwd, so a `cd` does not carry over and an `export` does. After a lane finishes, sweep your own shell; an inherited variable overrides the `-C`, `--profile` or `--kubeconfig` flag you trust to scope a command:

  ```bash
  env | grep -E '^(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|DOCKER_CONFIG|AWS_PROFILE|KUBECONFIG|GH_TOKEN)='
  ```

  An inherited `GH_TOKEN` bypasses the gh shim's per-owner token routing, so ask a worker to run the same check before its first test run.
- Pass a credential to the one command (`env AWS_ACCESS_KEY_ID=… aws …`), and run `aws sts get-caller-identity` before any mutation.
- For a test-gate line, report the interpreter path from the pytest header, not the command you typed.

## When the harness bash tool breaks

A subagent without an eval tool (`deep` has none) cannot "use eval instead". Define a tool in your own kernel and pass its name: `@tool def shell(command, timeout_seconds)` running `subprocess.run(["bash", "-lc", command])` and returning the exit code with both streams, then dispatch with `tools: ["shell"]` (or `agent(..., tools=["shell"])`). The tool keeps no state between calls.

That subprocess starts from the kernel's environment, not the box's. In an agent box it lacks `AWS_EC2_METADATA_SERVICE_ENDPOINT`, so AWS commands fail with `NoCredentialsError`: read the value from `/proc/<pid>/environ` of the box's own omp process and set it in the tool's environment.
