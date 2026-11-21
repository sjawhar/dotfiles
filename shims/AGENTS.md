# shims

Each executable here shadows the binary of the same name (`.bashrc` and `mise.toml`'s `_.path` put
this directory first on PATH) and adds behavior before it hands the call to the real one. Each
shim's header and comments say what it adds; this guide holds the rules that span shims.

## Test

- CI runs none of these. All cases, from the checkout root: `uvx pytest shims/tests`; in parallel,
  `uvx --with pytest-xdist pytest -n 8 shims/tests`.
- One case: `uvx pytest shims/tests/test_ssh_keygen_shim.py -k test_signing_runs_without_the_agent`.

## Where the header is not enough

- `gh`, for an agent, routes a call through `knives gh` to the owner's GitHub App, or to the
  upstream token `GH_PUBLIC_REPO_PAT` for an owner with no installation, and gives gh the
  placeholder login of `scripts/gh-agent-config`. Text passes `public-text-gate` and the
  leak-term check (`scripts/`).
- `tmux` also marks what an agent starts and refuses its server-stored commands ("Who is an agent").
- `person-names` is data: names `scripts/person-branch-guard` refuses at the start of a branch name.

## Adding to a shim

A shim composes with everything else that wraps its binary or already does what it adds. Before
you add a side effect or a lookup, search for callers that already do it and for every other
wrapper of the binary: a lock can collide with theirs, a lookup can find one that hands it back.

- Test a shim from a clone and write it atomically (root `AGENTS.md`, "The live checkout"): every
  session runs the shims in `~/.dotfiles` as saved, and a shim truncated in place exits 0 silently.
- Take the real binary as the first match past the shim's own PATH entry (from the top only when
  the shim runs by a path outside PATH), and skip any file whose real path is in a `shims/`
  directory, as the loop in `shims/az` does.
- Exceptions: `jj` and `tmux` search from the top and pass the whole PATH on (jj's git must reach
  `shims/git`; panes inherit the server's PATH), and stop loops with `DOTFILES_JJ_SHIM` and
  `DOTFILES_TMUX_SHIM` (`gh`: `DOTFILES_GH_SHIM`). `gcloud` searches from the top and skips copies.
- Keep a shim's marker in its first 512 bytes: `knives-gh-shim`, `git-worktree-shim` (also read by
  `scripts/agentbox`), `jj-lock-shim`, `tmux-guard-shim`. Without it the shim runs itself.
- There is no sourced library: `agent_context`, `mise_paths`, `is_mise_shim` and `mise_pinned` are
  copied into each shim that uses them. Change every copy; `test_shim_agent_context.py` and
  `test_shim_moved_home.py` fail on drift.
- A test that runs a shim, jj or an editor starts it with `LeakTestCase.launch`
  (`test_leak_terms.py`): a cgroup with a task limit and a timeout, so a loop fails only the case.

## A moved HOME

A caller can give a shim its own HOME and a bare PATH: a Legion test pane runs with
`HOME=<temp dir>` and `PATH=<bin>:/usr/local/bin:/usr/bin:/bin`, `<bin>/jj` a link to `shims/jj`.

- Take the checkout from `DOTFILES_DIR`, else the shim's own `readlink -f` path, and mise, its
  configuration and data dirs from the shim's copy of `mise_paths`; never through HOME or the
  path the shim was run by.
- Keep state the caller's HOME owns (caches, the gh agent config, tmux sockets) under HOME.
- `shims/tests/test_shim_moved_home.py` runs each shim that way; add a case for a new lookup.

## Who is an agent

- A caller with a session marker (the list in `agent_context`), the gh-app routing include, or
  an SSH command whose session has no terminal. `AGENT_SHIM_PERSON=1` lifts the SSH rule only.
- `az`, `gcloud`, `gh`, `git`, `jj` and `tmux` hold identical copies of `agent_context`. A new
  marker also goes in `shims/secrets`, `shims/gh-app-token`, the list `shims/tmux` carries into
  new panes, and `MARKERS` in `shims/tests/test_shim_agent_context.py`.
- tmux decides nothing: a pane takes the server's environment, so `shims/tmux` and
  `scripts/agentbox-tmux-guard` carry the markers into it.
- Tests run each case with `run_without_tty` or `run_with_tty` (`shims/tests/stdin_cases.py`).
  A person's case drops `SSH_CONNECTION`; one that commits brings its own HOME, git config, author.

## Leak-term list

- `leak-terms.sops.yaml` is encrypted to `.sops.yaml`'s default rule. Edit it only with
  `sops edit leak-terms.sops.yaml`; never put a term in a file, an argument or the environment.
- `terms`: lines for every repository. `scoped`: repository patterns (`owner/name`, shell
  wildcards) to lines, read by a push to or gh text for a matching repository. `allowed`:
  patterns to exact tokens, `#` lines giving the reason; a match wholly inside one is no match.
- gh text also reads each knives `repos.toml` and `repos.local.toml` entry's `forbidden` list.
- A term matches anywhere, inside words too, case ignored; a run of spaces matches spaces or tabs.
- `scripts/leak-terms-check` is the one matcher; a caller treats any nonzero exit as a refusal.
- A refusal prints the text that matched, so none may reach a public place (a post, a public
  CI job) until it shows each match's kind (`<term>`) in place of the text.
- The record (dispatch://INFRA-5140): `leak-terms-check record --url URL REV...` reads each file
  version (blob) in REV's trees whole, as code and as prose, with no allowed token, and notes each
  that holds no term in `refs/notes/leak-terms-check`, as `pass <key>`. The key names the script's
  bytes, Python's version, its Unicode version, the encrypted list's bytes and URL's repository.
  A push to URL then reads no content of a noted version, and reads every message, path, tag,
  ref name and other file as before. Exit 0, or 2 with nothing recorded (run it again).
- Whoever can write `refs/notes/leak-terms-check` in the pushing clone, or where it fetches it
  from, can make a push skip a file's content: a note is trusted as written.
- Tests get an isolated list from `shims/tests/leak_terms_fixture.py`; a test that builds its
  environment from nothing uses `leak_check_environment(path)`.
