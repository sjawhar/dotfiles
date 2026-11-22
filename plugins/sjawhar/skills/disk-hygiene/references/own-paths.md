# Cleaning up after yourself

Read this when you clean up after yourself, and before you make a work directory, keep work in `/tmp`, or make a second tree of a repository. `S` is set in SKILL.md.

- Make each work directory that must outlive your `TMPDIR` with `work-dir <name>`: its `OWNER` file tells `devbox/session-scratch-reap` whose it is.
- Never keep work in `/tmp` on sami-rocqperf: it is the shared root disk and is emptied at every boot.
- Make a second tree of a repository already on the box with `~/.dotfiles/scripts/agent-workspace --separate-repo` or `git worktree add` from your clone, never a full clone.
- Remove such a second tree when its work ends.
- In an agent box, your box's tree (minus its bind-mount sources) and `/tmp` are yours or your subagents'.
- In an agent box, any other path is yours only if your or your subagents' transcripts (the `session-attribution` skill) show the command or write that made it.
  - If no such transcript shows it, leave that path and report it.
- In your own box, also run `$S containers` and `$S images`: a `registry` container there is yours.
- A core file is crash evidence first: identify it (`file core`, `gdb -batch -ex bt core`) before you delete it.
  - A core file's backtrace names the tool that crashed; a matching restart time does not.
  - In a box, where `dmesg` is unreadable, a core file can be the only evidence.
