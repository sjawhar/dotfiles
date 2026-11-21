# omp

- Extension tests: `bun test omp/extensions/__tests__/`
- Test a condition rule with `omp ttsr test '<text>' --rule omp/rules/<name>.md --source tool --tool <tool>`. `--tool` defaults to `edit`, so a rule scoped to `tool:bash` matches nothing unless the test names `--tool bash`.
- A new extension loads only once `installers/omp.sh` links its entry file, and a pull does not run that installer: after the change merges, run `bash installers/omp.sh` on each machine.
- `omp/agents/` holds omp's agent definitions and `plugins/sjawhar/agents/` holds Claude Code's, under the same names: a change to one does not reach the other.
- A key never goes in a committed file, in a session's environment, or on a command line, where any user on the machine can read it. `omp/mcp.json` starts a server that needs a key through `secret-run` or `secrets`, which put the key in that server's environment.
- Change a plugin pin (`package.json`, `bun.lock` or `omp-plugins.lock.json` in `omp/plugins/`) only in a clone, by a commit. A plain `bun install` or `bun add` in a machine's `~/.dotfiles` rewrites the pins, and `scripts/dotfiles-follow` then refuses to move that checkout.
- A pin bump redoes each patch in `omp/plugins/patches/` (`bun patch <name>`, then `bun patch --commit node_modules/<name>`): Bun only warns about a patch whose key no longer matches, installs the package unpatched and exits 0. Drop a patch only once the pinned version carries its fix.

Where this file and the code disagree, the code is right: follow the code and correct this file.
