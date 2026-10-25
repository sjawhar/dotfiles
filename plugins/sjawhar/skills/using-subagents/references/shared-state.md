# What your subagents share with you

## Pin a lane's environment; never let it inherit one

Name the venv, the cwd and the config path explicitly per invocation, and treat `*_PROJECT_ENVIRONMENT`, `PYTHONPATH`, `VIRTUAL_ENV`, `DOCKER_CONFIG` and every other ambient pointer as untrusted input. Before trusting a measurement, print the path the tool resolved and check it is inside your own workspace. Also reproduce CI's environment: CI has fewer variables and tools than a developer box, and some defects appear only there, so a brief that names a CI lane's file names the lane's environment too — as an instruction ("run it once with the region unset"), not as background.

## The eval kernel

`task` subagents share the parent's eval kernel; eval `agent()` children get their own. Sibling `task` agents overwrite each other's top-level names in the shared kernel, prelude helpers and skill snippets included (e.g. `output`, `note`), and one timed-out cell interrupts all of them. Prefixing names is not enough. Rebuild state from files on disk before each write, assert that an output's keys equal its input chunk's, run long work as detached subprocesses, or hand the work to `agent()` children.

`task` subagents also share the parent's loaded extensions, so the tool descriptions they read are the ones from when the parent session started.

## The shell

With the harness `bash` tool, every subagent's calls run in the parent session's one persistent shell — `agent()` children included — so an `export` from any of them reaches all of them. That is why the five rules ban `export` instead of describing a variable's lifetime. The working directory is not shared: a subagent's `cd` leaves the parent's `pwd` unchanged, because every call is reset to the session cwd.

Sweep by name in your own shell after a lane finishes, because an inherited variable overrides the `-C`, `--profile` or `--kubeconfig` flag you trust to scope a command:

```bash
env | grep -E '^(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE|DOCKER_CONFIG|AWS_PROFILE|KUBECONFIG|GH_TOKEN)='
```

`GH_TOKEN` is in the list because an inherited one stops the gh wrapper from picking a per-owner token: it sends that one token to every owner, so a token minted for one organisation returns `Bad credentials` against another. Ask a worker to run the same `env | grep <VAR>` before its first test run. Never `export` a credential — pass it to the one command (`env AWS_ACCESS_KEY_ID=… aws …`) — and run `aws sts get-caller-identity` before any mutation. For a test-gate line, report the interpreter path from the pytest header, not the command you typed.

## Background work

Anything that must outlive a tool call runs under `hub start` (five rules, rule 5), never a bare `nohup cmd &`: in this harness `nohup` is a shell builtin, `$!` comes back empty and the process dies silently when the call ends. Where `hub start` is unavailable (it fails with `broker.sock` ENOENT in some boxes), use `setsid cmd </dev/null >log 2>&1 &`, which survives the call.

## When the harness `bash` tool breaks in a box

Telling subagents to "use eval instead" does not work: they have no eval. Define a tool in your own kernel and pass its name through — `@tool def shell(command, timeout_seconds)` running `subprocess.run(["bash","-lc",cmd])` and returning the bare exit code with both streams, then `agent(..., tools=["shell"])`.

The trap inside the remedy: that subprocess starts from the kernel's environment, not the box's, so it does not inherit `AWS_EC2_METADATA_SERVICE_ENDPOINT` and every AWS-backed command under it fails as `NoCredentialsError` — a failure that names credentials rather than the environment. Read the value from `/proc/<pid>/environ` of the box's own omp process and set it in the tool's environment. A kernel-hosted shell keeps no state between calls, so lanes using it are not exposed to the shared-shell problem above.
