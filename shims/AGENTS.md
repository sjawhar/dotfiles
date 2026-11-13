# shims

PATH-priority wrappers: each executable file here shadows the binary of the same name and adds
behavior before handing the call to the real one. One file is data: `person-names`, the name list
`scripts/person-branch-guard` reads (under `git` below), which is not executable.

## What each shim adds

- **`gh`** sources the auth token. In an agent session (omp, agentbox, or anything launched
  with the gh-app routing include) it routes every call to the repo owner's GitHub App; an
  owner with no App installation (METR, can1357, every upstream we fork) gets the upstream
  token from the secrets store (`gh-app.agent.fallback-secret`, `GH_PUBLIC_REPO_PAT`). No
  agent process reaches the user's own login, through this shim or past it: gh runs with
  `GH_CONFIG_DIR` at `scripts/gh-agent-config`'s directory, whose placeholder login stands
  where the user's would be, so gh never reads his token from `hosts.yml` or the keyring.
  There gh runs only with the token knives routed (`scripts/gh-routed-only`, which the shim
  gives knives as its gh): a call that needs GitHub and got none (no repository in the call,
  in `GH_REPO` or in the directory's remote) is not sent; it exits 4 with one
  `gh shim: NOT ROUTED, so not sent` line naming the fix, and the call log records
  `unrouted-refused`. It could only have got the placeholder login's 401, which a caller that
  drops stderr never reads (2026-11-03, 18 mutations by node id from a directory with no
  repository). A token the caller set, and help/version/completion/config/alias, run as before
  (`shims/tests/test_gh_unrouted.py`);
  `scripts/agent-session` and `omp/extensions/session-env.ts` export that directory for the
  whole session (and omp's eval kernels, through `eval.envPassthrough`), and `mise.toml`'s
  `_.path` puts this dir first in every PATH mise builds, since mise's python3 shim put mise's
  gh ahead of it in omp's Python eval kernel, which approved a pull request as the user on
  2026-11-02. `gh auth` verbs other than `status` are refused, and a call GitHub refuses for
  the App (a review of the App's own pull request, a permission its installation lacks) ends
  with a `gh shim: REFUSED` block naming the call, the identity, why, and the route: where a
  repository requires an approval your own identity cannot give, ask the INFRA Legion
  controller for a review issue; elsewhere a verdict goes out as an App
  comment beginning `VERDICT: <verdict> at <head sha>`. A post
  (`issue create`, `issue
  comment`, `pr comment`) Sami instructed in a Dispatch ask he answered runs again with
  `GH_SHIM_APPROVAL=dispatch://<KEY>/ask/<id>`, which `scripts/gh-approved-post` checks. Every
  call GitHub refuses for the App, those included, also gets the admin route: a GitHub admin
  action that work Sami already asked for needs (an App permission, a CODEOWNERS approval in
  his name, an admin merge, a repository or branch setting) is done by the agent itself with
  Sami's admin token from the credential broker, never asked of him (he said so five times,
  2026-10-25 to 2026-11-03, last on dispatch://OSSV-300/ask/dc4e1c38-bd15-4999-9ef5-4c6540a2ce9a).
  The block prints the refused call ready to run:
  `secret-run --register --reason '<what and why>' GITHUB_ADMIN_PAT -- sh -c 'GH_TOKEN=$GITHUB_ADMIN_PAT exec gh "$@"' gh <the call>`
  for theorem-labs, `GITHUB_PERSONAL_ADMIN_PAT` for sjawhar's own repositories; the `sh -c`
  is there because the broker exports a key under its own name, which gh does not read. Sami
  approves the broker's grant card once per session; the card is not an ask. `--register`
  is the route's fallback for a session with no broker identity: one launched past `shims/omp`
  (on 2026-11-01 a shell's PATH put mise's omp ahead of `shims/`, so `scripts/agent-session` never
  ran), where `agent-secrets` answers `NOT_A_SESSION` and plain `secret-run` reads secretsd and
  gets "not found". It registers that one command as a broker session of its own, with no
  restart, and changes nothing where the session has an identity. Anything else,
  and any refusal of an inherited `GH_TOKEN` or an upstream token, stops and goes to the lead
  (`shims/tests/test_gh_user_login.py`). A call GitHub stops
  for a spent budget (`API rate limit exceeded`, a secondary limit, HTTP 429) ends instead with
  a `gh shim: RATE LIMITED` block: the identity, the budget the call spends (core, graphql,
  search or code_search, read from the free `rate_limit` endpoint on the same route, with at
  most 30 s for that read) and every budget at 0 with its reset time, and the advice to wait
  for the reset rather than look for another credential. The shim does not retry; gh itself
  does not either, so the call fails in seconds (measured 2026-11-03: 3.5 s through the shim,
  0.2 s for gh alone). Until 2026-11-03 the block named only core and graphql, so a spent
  search budget read as core with thousands left. Outside an agent
  session, `gh auth git-credential` (git's helper in `~/.gitconfig`) goes to the user's login,
  except where the machine's own git config file, `~/.config/git/config` (not the shared
  `~/.gitconfig`, not a repository's), names `gh-app.host-profile` (a machine with no person's
  login, sami-devbox: `installers/gh-app-host.sh`): there it answers through
  `gh-app-token <profile>` as an agent's does (`shims/tests/test_gh_host_credential.py`).
  On that machine every other gh call outside an agent session that carries no `GH_TOKEN` or
  `GITHUB_TOKEN` gets an installation token of the same profile for its target owner
  (`gh-app-token <profile> --owner OWNER`, through the broker's machine login): the owner from
  `-R`/`--repo`, `GH_REPO`, a `repos/OWNER/…` path of `gh api`, or the directory's origin remote,
  else theorem-labs. Only a `ghs_` App token is used; for an owner the App does not cover the call
  runs with no token, never the profile's personal fallback. Before this a host shell's gh had no
  token there: `installers/agent-spend.sh` and the pre-push leak check failed (2026-11-04,
  `shims/tests/test_gh_host_token.py`).
  `gh pr create` for an
  agent-c pull request that changes only documentation is refused unless its head is the
  day's `docs-batch/<topic>/<UTC date>` branch and that batch has not merged yet
  (`scripts/docs-pr-gate`). In an agent session every call that reaches gh first goes to the shared
  public leak check through `scripts/public-text-gate`, which refuses it before gh runs. The check
  runs once per call, on the pass that runs gh: with the read cache on (below), the cache's
  re-entry, on exactly the arguments gh then gets. A read the cache answers sends nothing and runs
  no check. Until 2026-11-03 the check also ran ahead of the cache, so twice per call (4.6 s p50
  per run over 20 calls at load ~290). When the check asks for the
  text gh would read from stdin (its exit 4: `--input -`, `--body-file -`, `-F name=@-`), the
  shim reads all of stdin (at most 64 MiB) into a private file it unlinks before writing a
  byte, hands that open file to the check as `PUBLIC_LEAK_CHECK_STDIN=/dev/fd/N` (a value its
  caller set is cleared first), and gives gh the same bytes only when the check passes. The
  shim's stdin buffer has no pathname after it is opened; killing the shim leaves no named shim
  stdin buffer (`shims/tests/test_gh_public_text_gate.py`).
  It also logs and caches, because every session on a host shares the App installation's
  one primary rate limit: on 2026-11-01 that limit reached zero four times and every
  session that reads GitHub stopped (dispatch://BET-303). Each OUTERMOST call (a re-entry
  from knives or from the cache carries a marker and is the same call) appends one JSONL
  line — time, pid, session id, cwd, argv — under `<cache root>/log/`, where the cache root
  is `/mnt/nvme/<user>/gh-read-cache` on a machine with that disk and
  `${XDG_CACHE_HOME:-~/.cache}/gh-read-cache` on one without, 0700 either way. A READ then
  goes through `scripts/gh-read-cache`, which answers an identical read from any session
  inside 60 s with no request at all, and revalidates a plain `gh api` GET with its stored
  ETag — a 304 does not count against the primary limit. Writes are never cached: the
  classifier is an allow-list of read verbs and a GET-only rule for `gh api`, and anything
  else, any failure and any unexpected output shape runs the command as written. A write
  that goes through (a `gh pr`/`gh issue` verb that is not a read, `gh run rerun`, a `gh api`
  call with a method other than GET or with fields, a GraphQL mutation) also stops the cache
  serving, from the moment it ends, every read of the same pull request or issue, every
  repository-wide read of its repository and every cross-repository read (`search`,
  GraphQL); a write it cannot place in one number clears the repository, and one it cannot
  place in a repository clears everything. Without that, a session read its old pull-request
  body back for up to 120 s after a successful `gh pr edit` (three sessions on 2026-11-02).
  A body saying GitHub has not computed mergeability yet (`"mergeStateStatus":"UNKNOWN"`,
  `"mergeable":null`) is never stored, since the answer to it is to read again. Only one
  process asks GitHub a given question at a time: the second of two overlapping identical
  reads waits up to 30 s for the first one's answer and then makes its own, so it can
  never inherit a failure. `pr view`, `pr checks` and `pr list` with `--json` reuse for
  120 s rather than 60 s, since the gate loops on this host poll every 30 s and every
  120 s and a 60 s window misses the slower one by construction (measured 2026-11-01: an
  identical read inside 60 s was 11.7% of calls, inside 120 s 19.9%).
  `GH_READ_CACHE=0` turns the cache off (a write from such a session then clears nothing),
  `GH_READ_CACHE_LOG=0` the log,
  `GH_READ_CACHE_DEBUG=1` prints one stderr line per hit, and
  `scripts/tests/test_gh_read_cache.py` is the contract.
  The cache ends with the process that started the shim: the shim passes its parent's pid as
  `GH_READ_CACHE_PARENT`, the cache asks the kernel for a TERM when that parent dies
  (`PR_SET_PDEATHSIG`) and refuses to start under any other parent, and it runs each gh child in
  a process group of its own, which a TERM, HUP or INT to the cache ends whole (the shim, knives,
  gh and the leak check under it). Before 2026-11-03 a `timeout` that killed the shim left the
  cache running as a child of pid 1, a GraphQL read still going 2.5 min later.
  The real gh is the first gh past this dir on PATH. When that is mise's own shim, the gh pinned
  exactly in mise's global config runs straight from its install dir, as `shims/knives` runs
  knives: mise's shim is one more process per call, and under load mise hung for minutes
  (2026-11-03). mise's shim runs only when that install is missing
  (`shims/tests/test_gh_mise_pin.py`).
  A `gh api` call for a job's log (`…actions/jobs/<id>/logs`) gets
  `--allow-escape-sequences` unless it already carries it: a job log holds terminal escape
  sequences, which gh refuses to print, so the call returned nothing and the caller read no CI
  failure (11,209 such refusals on this fleet in the week to 2026-11-08). A log is text to read,
  not a document to render.
- **`jj`** locks the git worktree of every workspace `jj workspace add` creates, and where
  `scripts/omp-session-trailer` says no for the backing git repository (`git config
  omp.sessionTrailer false` there, or a throwaway repository, as for `git` below)
  runs every command that can write a description with `templates.commit_trailers` empty and
  `immutable_heads()` back at `builtin_immutable_heads()`: the session overlay's cross-session
  guard (AGENTC-318) tells a session's own commits by their trailer, so it is off there, and
  another session's unpushed commits in such a repository can be rewritten without
  `--ignore-immutable`. Read-only commands (`log`, `st`, `show`, `diff`, `op`, `bookmark`, `git`,
  …) skip the check.
  In an agent session `jj git push` first refuses a push that would create on a remote a bookmark
  whose name carries a person token, by the rule under `git` below. Each bookmark that jj's own
  dry-run plan adds (`add to …`) goes to `scripts/person-branch-guard` before anything is pushed,
  and a refusal carries jj's hint (`jj git push --named <issue-key>=<revision>`). A bookmark the
  plan moves or deletes is not checked, and neither is a tag or a push with `--dry-run`. The
  guard's exit 3 refuses the push and the shim exits 1. On any other non-zero status the shim
  prints `the branch-name check failed (exit <n>); the push goes on.`, and the push goes on. This
  check runs before the leak check below. The `git push` that jj then runs passes through
  `shims/git`, which checks it again.
  `jj git push` also runs the shared public leak check before sending anything: in an agent
  session always, and outside one (a person's own terminal, which no git hook reaches, since
  jj runs git with `--no-verify`) wherever this machine has the check installed, found as
  below (`PUBLIC_LEAK_CHECK`, `public-leak-check.sh` on PATH, then
  `~/.config/public-leak-check/path`; dispatch://BET-706). Outside an agent session a machine
  without the check is unchanged, and `DOTFILES_JJ_NO_LEAK_CHECK=1` skips the check for that one
  command, as `git push --no-verify` does for git; a refusal there ends with a line naming that
  variable. In an agent session the variable is ignored. The `--deleted` refusal and the
  trailer handling stay as they were. The shim resolves push aliases in jj's startup invocation
  context: up to eight levels in an agent session, up to 64 outside one. A command whose first word
  is a builtin other than `git` needs no alias lookup. Lookup/decoding failures and deeper
  expansions refuse (a person's refusal names `DOTFILES_JJ_NO_LEAK_CHECK=1`), since an alias the
  shim cannot resolve may be a push; outside an agent session a cyclic alias is left to jj, which
  refuses it itself and runs nothing. The checked expanded argv is the actual push argv. jj's dry-run resolves
  remote patterns and bookmark/tag selectors; each GitHub destination's full
  old/new ref updates go to the check's pre-push interface. Every destination must
  pass before any actual push. A failed dry-run, unreadable plan or nonzero check
  refuses; without a check, only a destination confirmed private passes (an agent session
  only: outside one, no check means no gate).
  Non-GitHub destinations pass unchanged. `--quiet` needs a second dry-run with
  only that rendering flag removed to read the plan. The gate does not depend on
  jj's git executable or git's hooks, and aliases cannot bypass `--deleted` refusal.
- **`git`**, in an agent session, refuses a bare `git worktree prune` and locks every
  `git worktree add`, so no box can prune another box's live registration. The prune runs
  when the repository's common directory, resolved through symlinks, lies under a temporary
  root by whole path components: `$TMPDIR`, `/tmp`, `/var/tmp`, `/mnt/nvme/<user>/session-tmp`,
  `/mnt/nvme/<user>/tmp-omp-batch` or `/mnt/nvme/tmp-omp-batch`. No other session can have a
  worktree in such a throwaway repository, and five oh-my-pi tests that prune one they made
  with `mkdtemp` failed in every agent session until it did. `-n`/`--dry-run` always runs. A
  `git worktree remove` of a tree carrying either agent add-lock (that one, or the one `jj` puts
  on; the exact reason, not one that only mentions AGENTC-431) runs with git's own double
  `--force`, so the lock goes with the registration and a removal git refuses keeps it; without
  a `--force` of the caller's the shim first runs git's own clean check (no checked-out
  submodule, nothing `git status` lists), which the double `--force` would skip. It finds the
  tree as git does: by a trailing part of its path that names one tree only, else by the path
  read from where every `-C` leaves git. Any other lock stands, and `scripts/agent-workspace rm`
  releases the same two locks from anywhere. It gives every
  commit an omp session makes its `Omp-Session` trailer, in every repository, public ones
  included, unless the checkout sets `git config omp.sessionTrailer false` or is a throwaway
  repository: no remote (`git remote` prints nothing) and its common directory, resolved
  through symlinks, under `$TMPDIR`, `/tmp`, `/var/tmp` or `/mnt/{nvme,scratch}/<user>/session-tmp`
  (`omp.sessionTrailer true` keeps it there too). A test that asserts its own commit message,
  such as oh-my-pi's pi-vcs `read_objects_logs_and_refs`, failed in every agent session until
  then (librarian ledger L1123); a new repository under `~/src`, and a temporary worktree of a
  repository with a remote, keep it. A repository it cannot read keeps it
  (`scripts/omp-session-trailer`, which jj asks too; `omp-session-trailer git` in a repository
  prints its answer). `git commit` gets it from git's own `--trailer`. `git merge`, `pull`,
  `cherry-pick` and `revert` run with `core.hooksPath` at `git-hooks/`: its prepare-commit-msg
  adds the line, and each hook name there then runs the hook git would have run without that
  override, a hooks directory the caller chose included (`git-hooks/hook`). A rebase, including
  `pull --rebase`, leaves replayed commits as they were;
  an alias, `git am` and `git commit-tree` add nothing. Commits omp writes through its own git
  library never run `git`, so no shim reaches them: omp appends `PI_COMMIT_TRAILERS`, which
  `omp/extensions/session-env.ts` sets to the session's line.
  A `git fetch <remote> <src>:<dst>` whose source is not a full ref name runs with `--no-prune`
  when no refspec has a `*`, since git's pruning deletes such a destination that already exists
  (`b:refs/remotes/origin/b` left it missing every second run on git 2.43.0) and prunes nothing
  else there; a glob refspec beside it, `--stdin` and `--multiple` leave the fetch as it was.
  In an agent session every `git push` that is not a dry run or a request for help, the push jj
  runs included, first goes through `scripts/person-branch-guard`, which refuses a push that would
  create on the remote a branch whose name carries a person token. A name carries a token when it
  starts with the token and then `/`, `-` or `_`, compared without case: `sami/x`, `Sami-x` and
  `sami_x` carry `sami`, and `sami` alone and `samizdat/x` do not. Only a branch counts, not a tag
  or another ref. The tokens are the value of `USER`, the part of `git config user.email` before
  its `@` and each word of `git config user.name`, each kept only when it has three characters or
  more, and every name in `shims/person-names` at any length (one per line, skipping blank lines
  and lines that start with `#`). That list holds `sami` and `sjawhar`: the guard looks up no
  GitHub login, so the login `sjawhar` comes from the list, and `sami` stays a token where `USER`
  and the git identity are unset.
  The guard first lists, from local reads only, every name the push could create: the push's
  arguments, the current branch and its `branch.<name>.merge`, each `remote.<name>.push`, and every
  local branch for `--all`, `--branches`, `--mirror` or a prefix git takes for one (`--bra`), a
  refspec with `@{` or a remote with `mirror` set. When no name in that list carries a token and no
  refspec holds `*`, it asks the remote nothing. Otherwise it reads the remote once, within 10 s:
  git's own dry run of the same push. Its flags, `--dry-run --dry-run --porcelain --no-verify
  --no-quiet --no-signed --no-recurse-submodules`, go just before the caller's first `--` or
  `--end-of-options` (the split), or at the end when there is none, so no caller option comes
  after them and a caller's `--no-dry-run` or `-q` changes nothing. git gives an option that takes
  a value exactly the next argument, so a caller's option left with none (`--repo` at the end, or
  `-o --`, whose `--` git takes as the value) takes the first, spare `--dry-run`, and the rest
  still apply. The split follows `--no-recurse-submodules`, which takes no value, so git reads it
  as the end of options and each argument after it as the remote or a refspec. It refuses each
  branch that a destination reports as new and whose name carries a token, and a branch the remote
  already holds passes. When that dry run fails before any destination answers, the guard prints
  one line and the push goes on: `git's dry run of this push gave no answer within 10 s, so the
  branch-name check checked no name; the push goes on.` when the 10 s bound ran out, else `git's
  dry run of this push failed (exit <n>), so the branch-name check checked no name; the push goes
  on.` Neither line names a cause, because the guard cannot tell a failure in git (a refspec that
  matches nothing) from one at the remote (a refused connection, a missing path). So only a push
  whose list carries a token, or whose refspec holds `*`, pays a remote read: one, and up to two
  for a jj push (`shims/jj`'s, then this shim's). Every other push pays only local reads.
  A request for help pushes nothing and is not checked: `-h`, alone or in a cluster of short options
  before any `o` (`-uh`, `-hv`, not `-oh`, whose `h` is the value of `-o`), or `--help`, before the
  first `--` or `--end-of-options`, lets git print its usage or open the push help as it would.
  A dry run (`-n` or `--dry-run`, unless a later `--no-dry-run` clears it) is not checked, and that
  is also how the guard's own read passes this shim when it comes from `shims/jj`. Each prefix git
  takes for one of those options counts the same: `--dr` to `--dry-run`, `--no-dr` to
  `--no-dry-run`. The argument after `--repo`, `--receive-pack`, `--recurse-submodules`, `--exec`
  or `--push-option`, or after a prefix git takes for one (from `--rep`, `--rece`, `--recu`, `--e`
  and `--pu` on), is that option's value and never makes a dry run. `--end-of-options` ends the
  options like `--`, for this reading and for the guard's: git reads each word after either as the
  remote or a refspec. A person's session and every command that is not a push are not checked
  either.
  The guard exits 0 to let the push go on and 3 to refuse it, and on 3 the shim exits 1. On any
  other status the shim prints `the branch-name check failed (exit <n>); the push goes on.`, and on
  a guard that is missing or not executable it prints `the branch-name check is missing (<path>);
  the push goes on.` In both cases the push goes on. Each line of a refusal starts
  `error: git shim: ` (`error: jj shim: ` for the push jj runs). It names each refused branch, the
  destination URL with any user part taken out, the token and the token's source. Then come the
  Workspace bullet of `.claude/CLAUDE.md`, word for word, and a hint to push the same commit under
  a name that names the work, never to rename (`git push <remote> <src>:refs/heads/<issue-key>`,
  or `jj git push --named <issue-key>=<revision>` for jj). The last line is `Nothing was pushed.`
  Not seen by the guard: a git alias for `push`; a branch made through the GitHub API or web page;
  a git run by its absolute path, and a jj not run through `shims/jj` whose git is not `shims/git`
  (run by its absolute path with no `shims/` on PATH, or with a `git.executable-path` that names
  another git); initials, and identity tokens under three characters; a token that is not at the
  start of the name, or that is followed by `.`; jj's own identity settings (`user.*`); a push whose
  first `--` or `--end-of-options` is the value of an option and has after it any word git reads as
  an option, `--` or `--end-of-options` (`git push --repo -- -v origin …`, `git push -o -- -- origin
  …`): the read takes that word as the remote or a refspec, fails, and the push goes on.
  It refuses wrongly where `USER` names no person. Every agent box sets `USER=ubuntu`, so a new
  `ubuntu-…` branch there is refused, and `USER=root` makes `root` a token the same way. The
  message names the token and its source (`USER`). In an agent session, the push that
  `agentbox move` makes of a moved tree's checked-out branch to the target machine can be refused
  when that branch is new there and its name carries a token. The move then logs that the branches
  were not all pushed and goes on. The tree still moves: its HEAD goes as
  `refs/agentbox-move/<hash>`, which is not a branch, and the target makes the branch from it with
  `update-ref`. `tests/test_person_branch_guard.py` is the contract.
  The shim also gives a `git push` that jj runs the pre-push leak check that a git push from the same
  checkout gets. jj runs `git … push --porcelain --no-verify … -- <remote> <sha>:<ref>…` once
  per remote, so no hook runs for it. When the process that ran git is jj and the checkout is
  guarded, the shim drops `--no-verify` and adds a last `-c core.hooksPath=jj-push-hooks/`. A
  checkout is guarded when some URL its `git remote -v` prints (read whole after the tab: a push
  line without its final ` (push)`, a fetch line up to its first ` (fetch)`, since a partial
  clone's filter, which may hold anything, follows that; a remote listed with a blank URL, its
  name and a tab only, has no url in its config, and git pushes to its name after
  `url.<base>.insteadOf` and `pushInsteadOf` rewrite it, so its URLs are those
  `git remote get-url --push --all <name>` and `git remote get-url --all <name>` print, and a
  failure of either is unknown) is a
  GitHub repository that is public or whose visibility cannot be read, or has a host part that
  cannot be parsed, as `scripts/github-repo-visibility` reads it: a leading `<transport>::` and
  `git+` stripped, github.com, www.github.com or ssh.github.com (one trailing dot allowed)
  directly or through an ssh alias (`ssh -G [-p PORT]` with the URL's user; an alias is unknown
  when git would run its own ssh command: the checkout's `core.sshCommand`, `GIT_SSH_COMMAND` or
  `GIT_SSH`), the leak check's fresh cache entry, then gh. A private or internal repository,
  a local path and another host are not GUARD (dispatch://BET-706). The hook sweep
  (`sweep-hooks.sh`, beside the check) installs the hook by the same rule with its own reader.
  `jj-push-hooks/pre-push` makes the installed hook's call, `<check> <remote> <url>`, with the URL
  git is pushing to and the update lines git is about to send; the check, found the way the hook
  finds it, alone decides, for every target. Every other hook name there links to
  `jj-push-hooks/hook`, which takes the shim's `-c` back out of `GIT_CONFIG_PARAMETERS` and runs
  the hook git would have run without it, so the override changes only which pre-push runs.
  Unknown counts as public: a cache that cannot be reached or read, an entry that is empty or
  holds another word, gh that fails or gives no answer within 5 s, guards the checkout, and the
  check then refuses a push to a theorem-labs repository whose visibility it cannot read either.
  Each refusal line starts with `error: `, and the shim leaves out the one "failed to push some
  refs" line git writes right after the gate's refusal, so jj shows the commit and the matched
  text even under `--quiet`; another destination's failure keeps its line. Not seen, as `git push
  --no-verify` is not: a jj told to run another git (`git.executable-path`) or copied under
  another name, and a remote changed between the shim's decision and git reading its config.
  A plain `git push` runs the pre-push hook of git's effective hooks directory (`core.hooksPath`,
  else the common hooks directory). The check reaches it there only through the leak-check wrapper
  (marker `PUBLIC-LEAK-CHECK-WRAPPER`) or the global hooks dispatcher
  (`~/.local/share/public-leak-check-hooks/global`, whose `dispatch` carries
  `PUBLIC-LEAK-CHECK-DISPATCHER`; `.gitconfig` includes it where step prepush of
  `devbox/sami-devbox-provision` installed it; dispatch://BET-706 R6). Neither reaches a fresh
  clone where the dispatcher is missing: an agent box made before 2026-11-05 has its `~/.gitconfig`
  bound to a deleted file and no hooks directory, and sees only these shims and the installed check.
  Nor does either reach a repository whose own `core.hooksPath` (husky, the pre-commit framework; an
  empty value too, which git 2.43 reads as `/`) overrides the dispatcher. So for every plain push in
  an agent session from a guarded checkout, with no `--no-verify`, whose effective hooks directory
  has no executable `pre-push` that is the wrapper or the dispatcher's entry (git ignores a hook that
  is not executable), the shim adds the same last `-c core.hooksPath=jj-push-hooks/` and names that
  directory in `GIT_SHIM_OWN_HOOKS`; the gate's `pre-push` then runs the check, and after a clean
  check that directory's `pre-push` with the same arguments and stdin, without the shim's `-c` or the
  variable. A hooks path the caller sets on the command line or in the environment is left alone.
  Where the wrapper or the dispatcher is effective the shim adds nothing, so each push is checked
  once: by the wrapper, the dispatcher or the gate.
  `tests/test_jj_push_leak_gate.py` is the contract; when the sweep is beside the check it runs
  the sweep's own code on the same checkouts, and fails when its decision and the shim's differ.
  Its dispatcher cases run the tag's `install-global-hooks.sh` from `LEAK_CHECK_HOOKS_SOURCE`, else
  from `~/.local/share/public-leak-check-hooks`, and are skipped without one.
- **`omp`** applies mise's environment, the committed config overlay, mnemopi's recall weights,
  a `PI_SHELL_PREFIX` that turns errexit, nounset and pipefail off before each bash call (so
  one call's `set -euo pipefail` does not break later calls in the persistent shell) and a named
  profile's links (unless it holds `.no-shared-setup`, below), then execs omp through
  `scripts/agent-session` (below). It sets no
  Envoy or Dispatch connection/credential override and no provider key. The plugin reads
  `envoy.json`, and keys come from the profile's `models.yml` (`omp/AGENTS.md`). AgentBox's
  inherited `ENVOY_MACHINE_ID` identifies the physical host, not a profile connection.
  Each session runs in a transient
  systemd user scope with a hard memory cap and swap off: `OMP_MEMORY_MAX=<size>`, by default the
  larger of 24G and MemTotal/20 in whole GiB (dispatch://INFRA-898: the largest of 44 sessions
  measured on 2026-10-31 held 17.8 GiB; dispatch://INFRA-993: 24G on sami-rocqperf, 109G on
  sami-devbox), a caller's value clamped at four times the default. The shim waits for the session instead of exec'ing, and says on
  the pane when the cap was what killed it. A session inside such a scope exports
  `OMP_MEMORY_SCOPE`, so an omp it starts takes no default and shares the parent's scope. One
  session reached 138.0 GiB on 2026-10-26 and the box-wide OOM
  killer took two unrelated pods with it; inside a scope only that session dies. `MemoryHigh` is
  deliberately not set: it throttles a session into reclaim instead of killing it, which looks
  like a lane that is thinking.
  A machine whose sessions should have no cap says so in its own
  `${XDG_CONFIG_HOME:-$HOME/.config}/omp/memory-max`, outside this repo: `off`, or a size that
  replaces the default (clamped the same way). Oryx, Sami's personal machine, holds `off`: one
  assistant session with its subagents on 62 GiB, killed at the 24 GiB default with 30 GiB free
  (2026-11-04). The dev boxes have no such file. A caller's `OMP_MEMORY_MAX` wins over the file
  (`off` for one launch); a session with no cap exports `OMP_MEMORY_SCOPE=off`, so the omps it
  starts take no default either.
  Inside an agent box (`AGENTBOX_IDENTITY_FILE`, `/etc/agentbox-identity`) it asks for no scope at
  all: the box's own cgroup is already this session's ceiling, and the shim says that in one line
  instead of reporting a refusal the agent cannot act on, which was the first line of every boxed
  launch until dispatch://INFRA-702. It is now a decision rather than a refusal it accepts, since
  `shims/systemd-run` makes `--user --scope` work in a box: a probe there would succeed and wrap
  every boxed session in a second cgroup, sized from the HOST's RAM (a box reads the host's
  `/proc/meminfo`), above the box's own ceiling and under another cgroup than the box's.
  A named profile whose `agent/models.overlay.yml` exists is a shared profile: the shim links
  all of the default's state into it and rebuilds its `models.yml` as the default's merged with the
  overlay, on every launch, under a 30 s lock; a real entry on both sides stops the launch
  (`omp/AGENTS.md`, "A shared profile").
  A named profile whose directory holds `.no-shared-setup` gets neither the mirrored links nor a
  shared profile's setup: the shim writes nothing there and runs no `omp-models-check`
  (`omp/AGENTS.md`, "An opted-out profile"; `scripts/tests/test_omp_profile_links.py`).
  The profile is the last `--profile`, else a defined `OMP_PROFILE`, else a defined `PI_PROFILE`,
  else the machine's own default: `${XDG_CONFIG_HOME:-$HOME/.config}/omp/profile`, outside this
  repo, holding a profile name. Oryx, Sami's personal machine, holds `personal`; the dev boxes
  have no such file and keep the default profile. The shim exports the file's name as
  `OMP_PROFILE`, so omp, its subagents, scripted `omp -p` runs and `ctx` see it, and
  `--profile default` still starts the default profile. omp drops `OMP_PROFILE` in a
  default-profile session, so on a machine with the file a launch that resolves to the default
  exports `OMP_IN_DEFAULT_PROFILE=1`, and an omp it starts with no profile named stays in the
  default (`scripts/tests/test_omp_profile_links.py`).
- **`claude`** and **`codex`** (a link to `claude`) export the model-spend environment of
  `scripts/agent-spend-env.sh` (dispatch://INFRA-165), then exec the first real binary of that name
  past this dir on PATH, skipping mise's shims dir and any file whose real path is in a `shims/` dir,
  else `mise which` it; arguments, stdin and PATH go through unchanged. They are for a claude or codex
  that a script or a session's bash tool starts by name (`claude -p` workers): those inherit their
  omp parent's collector and `OTEL_LOGS_EXPORTER=none`, and Claude Code sent nothing. Through the shim
  Claude Code gets `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`, traces and metrics off,
  no header (the collector's tailnet endpoint takes no token), and the project of its parent session's file (`OMP_SESSION_ID`);
  a different collector is kept as is. Codex takes only the tags from the environment and loses any
  inherited header: its exporter is the block agent-infra's `agent-spend-setup` (run by
  `installers/agent-spend.sh`) writes into `~/.codex/config.toml`. They do not exec
  `scripts/agent-session`, which `scripts/cld` still uses: that would also register every scripted
  worker as its own secrets-broker host session (a helper call and up to 10 s wait per launch) in
  place of the identity it inherits from its parent.
- **`knives`** execs the pinned knives (`"github:sjawhar/knives"` in `mise.toml`, looked up with
  `mise which -C / knives --tool github:sjawhar/knives`), or a local build on PATH past it,
  never a knives in a mise dir: a session keeps the PATH it started with, and that PATH names
  the install dir pinned back then. `-C /` keeps a project's own mise pin out, since the knives
  plugin every session loads is pinned machine-wide beside it. A third entry in one pid within
  10 s exits with an error; `knives gh` enters twice by design, through `shims/gh`. An exact pin
  that no install dir holds yet is installed by the shim itself (`mise install -C /
  github:sjawhar/knives@<pin>`, its log in `~/.cache/dotfiles-knives-shim/install-<pin>.log`),
  under a lock in mise's data dir that host and agent boxes share, and run as soon as its binary
  answers `--version` with the pin; mise goes on rebuilding every tool's shims in its own
  session, which took minutes more on a loaded box. A new pin goes live the moment `mise.toml`
  changes on a machine, and every gh call failed until a person installed it (3.2.2 on
  sami-leanperf and 3.2.3 on sami-rocqperf, 2026-11-03). A failed install is not tried again
  for 60 s; `DOTFILES_KNIVES_INSTALL_TIMEOUT` (300 s by default) bounds the wait for the binary
  and for the lock.
- **`ssh-keygen`** drops `SSH_AUTH_SOCK` for `-Y`, the operations git and jj sign and verify commits
  with: the signing key is a file, and ssh-keygen asks the agent first, so a stuck agent socket
  hung every signed commit. It execs the first `ssh-keygen` past its own PATH entry.
- **`systemd-run`** makes `--user --scope` work INSIDE an agent box, and changes nothing anywhere
  else. A box reaches the host's user manager through the session-bus relay, and that manager
  cannot adopt a process from the box's pid namespace, so every capped run in a box died with
  `Failed to start transient scope unit: Connection reset by peer` — and the skills and lanes that
  cap heavy work that way (dispatch://INFRA-602, omp's gate, REARM) ran nothing at all. Sysbox
  delegates the box's cgroup tree, so in a box the shim makes the cap itself: a child cgroup of
  the box's own cgroup root, the limits written into its cgroup v2 files, the command's process
  moved in before it execs, and the cgroup removed however the run ends. `MemoryMax`,
  `MemoryHigh`, `MemoryMin`, `MemoryLow`, `MemorySwapMax`, `CPUQuota`, `CPUWeight`, `TasksMax` and
  `IOWeight` become `memory.max`, `memory.high`, `memory.min`, `memory.low`, `memory.swap.max`,
  `cpu.max`, `cpu.weight`, `pids.max` and `io.weight`, with systemd's own values (`24G`, `512M`,
  `2GB`, `10%` of this box's RAM, `800%` of one core, `infinity`). The kernel then enforces it as
  it does a scope's: a run past `MemoryMax` is OOM-killed in its own cgroup, exit 137, and nothing
  else in the box is touched. A unit property a box has no unit to carry (`OOMPolicy`,
  `Description`, …) is named on stderr and the run goes on; **a resource limit it cannot apply, or
  an option it cannot honour, is refused before anything runs**, because a capped run that
  silently loses its cap is the failure it exists to prevent. The move into the cgroup is made
  with `sudo` (cgroup v2 asks the mover for write access to the common ancestor's `cgroup.procs`,
  and the box's root cgroup is root-owned); the command itself still runs as the session user. On
  the host, and for any call that is not `--user --scope`, it execs the first real `systemd-run`
  past its own PATH entry. `shims/tests/test_systemd_run_box.py` drives the whole shim against a
  cgroup tree in a temp dir (`AGENTBOX_CGROUP_ROOT`), and its last two cases run against
  `/sys/fs/cgroup` where the machine delegates one.
- **`secrets`** refuses every agent request for a human-tier key
  (exit 77), and names the broker command (`secret-run`) or, in a session the broker does not
  serve, `secret-run --register`, which registers that one command, and the `omp --resume` line
  that relaunches the session as one. secretsd's only prompt for such a key
  is a YubiKey blink the firmware holds about 15 seconds, and on 2026-11-01 thirteen agent
  requests timed out on it. An agent is a caller with `OMP_SESSION_ID`, `PI_SESSION_FILE`,
  `CLAUDECODE` or `ENVOY_SESSION_ID` set; every other call, and every call from Sami's shells,
  execs the first `secrets` past its own PATH entry with the same arguments. A shell that ran
  `secrets` before the shim existed has the old path hashed and bypasses it until `hash -r`.
- **`bq`** puts BigQuery's CLI on PATH at all. The Cloud SDK installs it beside gcloud in the
  SDK's own bin dir, which is on nobody's PATH here, so every caller named the SDK's full path
  instead (agent-infra's spend loaders, 2026-10-29). It takes the first `bq` past its own PATH
  entry (`shims/ssh-keygen`'s lookup), else the SDK's install location under the home gcloud uses, and
  turns gcloud's per-call file logging off, since bq shares that config dir and those logs.
- **`dispatch`** puts the Dispatch CLI on PATH at all. omp adds the pi-envoy plugin's bin dir to a
  session's PATH, but a login shell, a plain `ssh sami-devbox dispatch …` and a unit have no such
  entry. It execs `omp/plugins/node_modules/@sjawhar/pi-envoy/bin/dispatch` by its path: that
  script finds `dist/` from its own path, so a symlink to it fails with "dist/dispatch.js is
  missing" (sami-devbox, 2026-11-03). `devbox/sami-devbox-check` runs `dispatch --help` on the
  host and in a box.
- **`ctx`** reaches one of two memory graphs. Sami's personal deployment is chosen when the omp
  profile is `personal` (`OMP_PROFILE`, else `PI_PROFILE`; on oryx a bare `omp` sets it from the
  machine's `omp/profile` file) or the caller sets `CTX_PERSONAL_ROOT`: its root (that variable, else
  `ctx-personal` under an absolute `$XDG_DATA_HOME`, else `~/.local/share`) names its server in
  `server-url` and its release in `ctx-version` (an executable `bin/ctx` overrides it), and the
  shim sets `CTX_DATA_DIR` and `CTX_STATE_DIR` under it. An inherited `CTX_SERVER` is ignored
  there, because every shell inherits the team URL from `.bashrc`; only an empty one is kept, for
  a local tree read. Every other call gets the team server when the caller names none (an empty
  `CTX_SERVER` again means a local tree) and the mise pin (`"github:sjawhar/ctx-memory"` in
  `mise.toml`), found with `mise which -C /`, as `shims/knives` finds knives, so mise's own shim
  never hands the call back to it.
- **`pulumi`** names a state backend only when the CLI would otherwise find none. Pulumi CLI
  3.268.0 that reaches no backend at all, and recognizes a coding agent around it (it reads
  `CLAUDECODE`, `CURSOR_AGENT`, `CODEX_*`, `GEMINI_CLI`, `OPENCODE`, `AI_AGENT` and others),
  creates an ephemeral Pulumi Cloud account of its own, writes the claim in
  `/tmp/.pulumi/agent-claim.json` and prints a claim URL; one was created on this box on
  2026-10-28 by an agent that only ran `pulumi package add`. `PULUMI_BACKEND_URL` outranks a
  `pulumi login` and a project file's own `backend.url` rather than falling back to either, and
  this box hosts programs on more than one real backend, so the shim sets it (to `file://~`)
  only with all four true: `PULUMI_BACKEND_URL` unset or empty, `PULUMI_ACCESS_TOKEN` unset or
  empty, no non-empty `current` in the credentials file a `pulumi login` writes, and no
  `backend.url` in the `Pulumi.yaml`/`Pulumi.yml` the command would read (searched upward from
  the working directory, which `--cwd`/`-C` moves). It sets `PULUMI_SKIP_UPDATE_CHECK=true`
  either way, and is otherwise transparent: same argv, stdin, stdout and exit code, and no output
  of its own. It takes the first `pulumi` past its own PATH entry (`shims/ssh-keygen`'s lookup).
- **`az`** keeps an agent off the user's Azure login. The default config dir (`~/.azure`) holds
  Sami's own `az login`, so every az call an agent made without `AZURE_CONFIG_DIR` acted as
  Sami@Theorem.dev; three `az vm run-command invoke` calls did on 2026-11-01
  (dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec). In an agent session (the test
  `shims/gh` and `shims/git` use) with `AZURE_CONFIG_DIR` unset, the shim points az at
  `~/.azure-box-identity`, the machine's own managed identity, which holds the VM lifecycle and
  run-command rights the work needs on resource group `sami`. A machine whose box identity was
  never logged in gets one on that first call (`az login --identity` into that dir, no person
  needed); where no managed identity answers either, the call goes through unchanged unless
  `~/.azure` holds a user login, which is refused with the reason. An explicit
  `AZURE_CONFIG_DIR`, empty included, is kept, and outside an agent session nothing changes, so
  the user's own shell still runs as the user. It takes the first `az` past its own PATH entry
  (`shims/ssh-keygen`'s lookup), else the mise pin (`"pipx:azure-cli"` in `mise.toml`), and is
  otherwise transparent: same argv, stdin, stdout and exit code, no output of its own. On a
  Google VM (`scripts/on-google-vm`; sami-devbox, dispatch://INFRA-993), which has no Azure
  managed identity, the agent's dir holds the agents' own Azure identity `agent-azure`
  (theorem-labs/agent-infra#1146) instead: the shim runs `az login --service-principal
  --federated-token @/dev/stdin` into it with the VM account's Google ID token for audience
  `api://AzureADTokenExchange`, read from the metadata server and passed on stdin, and signs in
  again, under a lock, once the last sign-in is 20 minutes old (Google issues the token for an
  hour, and az keeps it as its client assertion). The client id comes from
  `AGENT_AZURE_CLIENT_ID`, else `~/.config/agent-azure/client-id` (the apply's
  `agentAzureClientId`); without either the call stops and says where it goes
  (`shims/tests/test_az_shim.py`).
- `gh-app-token`, `google-user-token`, `aws-cp`, `tmux`, `xdg-open`, `pyright`, `basedpyright`:
  see each file's header.
- **`gcloud`** (its header) decides gcloud's own identity: the active configuration `box`, the
  machine's service account. It does not reach a Google client library or Pulumi, which
  read `GOOGLE_APPLICATION_CREDENTIALS` and otherwise the well-known
  `application_default_credentials.json`, the user's own login on sami-rocqperf since
  2026-11-01T02:53Z. So `scripts/agent-session` and `omp/extensions/session-env.ts` export
  `GOOGLE_APPLICATION_CREDENTIALS` as the machine's `box_credential_config.json`
  (`installers/gcloud-box.sh`) for the whole session, eval kernels included; a file a caller
  named is kept, the well-known one excepted (`scripts/tests/test_agent_session_google_adc.py`).
  On a Google VM there is no such file and they export nothing: `box`'s account is the VM's
  attached one, and gcloud and every client library take its tokens from the metadata server,
  so the shim also refuses `auth application-default login|revoke` there whenever `box` is
  active, since the well-known file it would write must stay absent.
  On a Google VM, a config dir whose credential store still holds the Azure box credential (a
  copy of an Azure box's dir, such as a session's own `CLOUDSDK_CONFIG` that a session moved from
  sami-rocqperf brought along: LINUX-1248, 2026-11-03) loses that entry before the call runs,
  through `scripts/gcloud-drop-azure-credential`, and gcloud then takes the VM account's token
  from the metadata server; the shim reports the removal on stderr. Off Google nothing changes,
  and an Azure box pays no metadata lookup for it. The store is opened only when it is not the
  one the last check saw: its mtime, size and inode, which `stat` reads without opening it, are
  kept in a marker under `$XDG_RUNTIME_DIR` (else `~/.cache`) per config dir, together with that
  check's verdict, and an unchanged store reuses the verdict. The grep ran before every gcloud
  call before, which the Datadog CWS rule `theorem_gcloud_credential_read` reported as a
  credential-file read 109 times between 2026-10-30 and 2026-11-06, all of them this shim
  (theorem-labs/agent-infra#2328 proposed excepting the rule instead; an `exec -a ""` caller can
  hide an extra grep argument from argv parsing, so the exception was dropped and the reads
  were). A store that changed, or one with no readable marker, is checked exactly as before
  (`shims/tests/test_gcloud_shim_routing.py`, `GcloudAzureCredentialOnGoogleVm`).
  In an agent session the shim refuses, on every route, each call that writes or removes the
  well-known file: `auth application-default login|revoke`, and an `auth login` with
  `--update-adc` or a `--flags-file`. Only a routed bare login dropped `--update-adc` before,
  so an agent's login under the user's own configuration with `--update-adc`, started at
  2026-11-01T02:41Z, wrote the user's login there at 02:53Z. It also refuses a call whose
  account is a person's own login (`--account` or `CLOUDSDK_CORE_ACCOUNT`, anything but
  `*.gserviceaccount.com`), unless an explicit `--configuration` names the configuration that
  holds that login: the route an apply that no declared identity can make yet takes under Sami's
  standing permission, each use recorded (dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec).
  On 2026-11-02T00:07Z an exported account made one agent shell's kubectl create GKE Jobs as the
  user, and gke-gcloud-auth-plugin's cache beside the kubeconfig, keyed by context and not
  account, gave his token to every caller of it for an hour. Each refusal names the box
  identity, the CI route, agent-infra's applier `infra-applier@` impersonated from the box
  identity, and agent-infra's `docs/break-glass.md` for a step only a person can make, which the
  agent cites in a `dispatch ask` to that person; none names a sign-in command. The user's
  configuration's name is one variable in the shim, `USER_LOGIN_CONFIGURATION`. Outside an agent
  session nothing changes (`shims/tests/test_gcloud_shim_routing.py`). A call that another
  gcloud's hold on `credentials.db`, past the SDK's 5 s busy timeout, failed with exactly `ERROR:
  gcloud crashed (OperationalError): database is locked`, or with a `gcloud storage` worker's
  `ERROR: database is locked` line, runs again until 300 s after the call began
  (`GCLOUD_LOCK_RETRY_SECONDS`; its clock is bash's `SECONDS`, or the command
  `GCLOUD_LOCK_RETRY_CLOCK` names, which the tests set to a clock only their stub `sleep` moves,
  so their attempt counts do not depend on load), 0.2-6 s apart with jitter, and a repeat attempt first takes one
  of 8 host-wide slots per configuration directory (`GCLOUD_LOCK_RETRY_SLOTS`; flock(1) on
  `<config dir>/.gcloud-shim-retry-slots/<n>`, waiting in the kernel), so a stalled store queues
  its repeats instead of starting hundreds of SDK processes (2026-11-03 00:38Z: 67 calls gave up
  after 6 attempts in 40 s while the root disk was saturated). This applies
  when running it again repeats nothing: the reads (`config config-helper`, which kubectl runs,
  `auth print-access-token` and the others, and any group's `describe`, `list` or
  `get-iam-policy` before the command's own flags, such as `compute instances describe`, which
  failed transcript-mirror.service at 01:52Z on 2026-11-03), whose attempts are held back so only
  the last one shows, and `storage ls|cat|du|cp|rsync` and `storage objects|buckets list|describe`, whose
  stdout streams, so an attempt that wrote a byte of it is not run again. Never with
  `--delete-unmatched-destination-objects`, stdin (`-`, `-I`), or a pipe or device as a path;
  `storage mv` and `rm` never (the shim's `is_lock_retry_safe` gives each verb's reason). A
  command that changes anything (`create`, `delete`, `ssh`, …) runs once: the crash text is the
  same whether or not its request went out.
  Each attempt of such a call appends one line per kind of lock text it printed to
  `~/.local/state/gcloud-shim/locks.tsv` (`GCLOUD_LOCK_LOG` overrides the path):
  `<UTC time>\t<kind>\t<outcome>\t<subcommand>\t<pid>\t<omp session>\t<caller>`, kind
  `credcache` (the crash text), `store` (a `gcloud storage` worker's line; both with outcome
  `retried` or `gave-up`) or `token-cache` (any other `database is locked` line, the SDK's
  access-token cache warning; outcome `warning`). Lines from before `credcache` existed (before
  about 02:30Z on 2026-11-03) log both texts as `store`. The session is
  `OMP_SESSION_ID` (`-` outside omp); the caller is the program that ran gcloud and its script or
  `-m` module, never `-c` code. Count lock hits with `wc -l` or `cut -f2,3 | sort | uniq -c`.
  Calls outside the retry are not seen; a failed log write never changes the call.
  In an agent session (omp's `scripts/agent-session` exports `AGENT_SESSION_SCRATCH`) gcloud
  runs with `CLOUDSDK_CONFIG=$AGENT_SESSION_SCRATCH/gcloud`, a copy of `~/.config/gcloud` that
  `scripts/gcloud-session-config` makes at the session's first call (each sqlite store through
  sqlite's backup; caches, logs, the access-token cache and sqlite sidecars left out), so the
  session's sqlite stores are its own and on `/mnt/nvme`; `devbox/session-scratch-reap` removes
  the copy with the session's scratch. The shared stores on the root disk took 2,148 fsyncs and
  238 s of fsync time in 300 s on 2026-11-03, and the retry above logged 2,000-4,200 lock lines
  an hour. Left alone: a `CLOUDSDK_CONFIG` the caller set, an agentbox (its container binds the
  host's `.config/gcloud`), and a session with no scratch directory or a copy that cannot be
  made. A change that must outlive the session (`auth login|revoke|activate-service-account|
  application-default|configure-docker`, `config set|unset|configurations`, `init`, or a path an
  unknown flag makes ambiguous) runs on `~/.config/gcloud`, and the session's copy is set aside so
  its next call copies again. A sign-in made outside a running session reaches its copy only after
  the session runs one of those commands, or in its next session.
  In an agent session with `KUBECONFIG` unset or empty, `container clusters get-credentials
  <cluster>` writes `~/.kube/config.d/<cluster>` (mode 0700 directory) instead of `~/.kube/config`,
  and prints the `export KUBECONFIG=…` to use it; one whose cluster name it cannot read from argv
  is refused. `~/.kube/config` and its current-context are shared by every session on the machine:
  on 2026-11-02 an agent's get-credentials for `grade-us-west1` switched it to the grade cluster,
  and every other session's `kubectl create` in `builds-*` failed with "requires
  container.jobs.create". A person's shell and a caller that set `KUBECONFIG` are unchanged.
  A `gcloud storage` command runs with `CLOUDSDK_STORAGE_PROCESS_COUNT=4` (the shim's
  `GCLOUD_STORAGE_PROCESS_COUNT`) unless the caller set the process count: in the environment, an
  empty value included, in the configuration the call runs under, or in the SDK's installation
  `properties`. Left to itself the SDK starts 16 worker processes of 4 threads for a copy of many
  files on a 64-core box, and on 2026-11-02 at 21:11Z sami-rocqperf held 157 gcloud processes in
  groups of 16 while it swapped 53 GiB. Measured there that day, `storage cp -r` of 1,024 files of
  2 MiB: 16 processes took 21-30 s up and 12-18 s down at 18 processes and 1.8 GiB summed RSS;
  4 took 39-45 s up and 12-20 s down at 6 processes and 0.62 GiB. The thread count stays the
  SDK's: 8 threads instead of 4 gained nothing.
  In an agent session `compute instances create` and `compute instances bulk create` (also
  `bulk-create`, behind any release track) are refused unless the command line carries
  `--max-run-duration` or `--termination-time`, or the caller sets `GCLOUD_NO_RUN_LIMIT=<reason>`
  for a long-lived VM declared in code with an owner (dispatch://INFRA-672). A limit in a
  `--flags-file` or an instance template is not seen, so such a create is refused too. The refusal
  names the flags (with `--instance-termination-action=STOP` and, for local SSD,
  `--discard-local-ssds-at-termination-timestamp=true`), Google's run-time limit page and the
  escape; a call that passes keeps its argv. Outside an agent session nothing changes.
- **mise off PATH:** `omp`, `knives`, `pyright` and `basedpyright` call mise. A unit's, a test
  harness's or a git credential helper's PATH can hold this dir without `~/.dotfiles/bin`, where
  mise lives; these shims then run `~/.dotfiles/bin/mise` by its path, with `~/.mise` as its data
  dir when `MISE_DATA_DIR` is unset.
- **`gcloud`**'s global-flag table is the installed SDK's. After any `gcloud components update`,
  run `python3 scripts/gcloud-global-flags --check`; on a difference, regenerate the table with
  `python3 scripts/gcloud-global-flags`, and the test fixture with `--json`. Run it with `python3`,
  not `bash`.
- `gws`: an executable shim that resolves the gws-accounts wrapper installed by the omp plugin pin
  (`omp/plugins/package.json`). Its marker lets the package wrapper skip the shim while finding real
  gws; if the package is absent, the shim fails loudly and directs `bun install` in `omp/plugins`.

## Who is an agent

`az`, `gcloud`, `gh`, `git`, `jj` and `tmux` each hold a copy of one `agent_context` function,
and `secrets` the same test in POSIX sh. All seven read one marker set: `OMP_SESSION_ID`,
`PI_SESSION_FILE`, `PI_TOOL_BRIDGE_URL`, `AGENTBOX_SESSION`, `CLAUDECODE`, `ENVOY_SESSION_ID`,
`AGENT_SHIM_AGENT`, Legion's `LEGION_ROLE`, `LEGION_TREE`, `LEGION_CONTROLLER` and
`LEGION_DAEMON_URL` (`packages/daemon/internal/runtime/tmux/spawn.go`), and the gh-app routing
include. Until round 3 of dispatch://INFRA-1538 `secrets` read four of those and no routing
include, and no shim knew Legion at all. A caller is also an agent **when `SSH_CONNECTION` is
set and `SSH_TTY` is not**: `ssh sami-devbox az account show` carries no marker, and until
2026-11-07 every such command ran as the person whose login the machine holds — az with
`~/.azure`, Sami's own.

The SESSION decides, not the command's own stdin. sshd sets `SSH_TTY` for a session it gave a
terminal, and every command of that session inherits it, so `echo x | gh …` in a person's
interactive SSH shell, and git handing a credential helper its request down a pipe, stay the
person's; the first draft tested `[[ ! -t 0 ]]` and made both an agent's. A person's own shell
off SSH is unchanged, and `AGENT_SHIM_PERSON=1` is their escape for a deliberate non-interactive
SSH command of their own; it does not turn a session marker off.

**tmux decides nothing.** A pane takes the SERVER's environment, never the environment of
whoever asked for the pane, so an exemption for `TMUX` was a hole in both directions: an SSH
agent that started its own server got person panes, and an agent's `tmux new-window` under the
person's server got an unmarked pane that ran `gcloud --account <the person>` with no refusal
(round 3 of INFRA-1538, reproduced on a real server). The provenance travels instead, and
`shims/tmux` adds nothing at all to a person's own tmux command:

- **A command that makes a pane** — `new-session`, `new-window`, `split-window`,
  `respawn-pane`, `respawn-window`, `display-popup` — gets `-e <marker>=<value>` for every
  marker the caller holds, the whole `GIT_CONFIG_*` set when it holds the routing include, and
  `AGENT_SHIM_AGENT=1`. That last one is the provenance of an agent with no marker of its own,
  one the SSH rule alone classifies: without it, its pane under a person's server was the
  person's (round 5). Every command of a `\;`-separated list is treated, not only the first.
- **A command that runs a shell command** — `run-shell`, `pipe-pane` — takes no `-e`, so the
  command travels in a FILE and tmux is handed `env <marker>=<value> … /bin/sh <path>`. The
  directory is `$XDG_RUNTIME_DIR/tmux-shim`, else `/run/user/<uid>/tmux-shim` — which a cron
  job or a system unit still has though its environment does not name it — and nowhere else: a
  name in a shared temporary directory is another user's to guess and to plant a symlink at,
  which `mkdir -p` and `chmod` would then follow (round 8). It is made with `mkdir 0700` and
  checked with lstat — a symlink, another owner or any other mode refuses the call — and the
  file inside it is created O_CREAT|O_EXCL, mode 0600, named `agent-XXXXXXXX`. Its first line
  is `rm -f -- "$0"`, so it removes itself as the shell starts, and that is the ONLY thing that
  removes it. **A command tmux never starts leaves its file** (a bad `-t`, `pipe-pane -o` on a
  pipe already on). That residue is accepted: removing a file by age, or because the tmux call
  exited non-zero, would take the file of a command tmux has only QUEUED (`run-shell -b -d 5`,
  or a later command of the same list failing) and lose that work, and the residue is bytes of
  the agent's own command text in a private directory (0700) on tmpfs that logout and reboot
  clear (round 9). These are marked and not refused because `scripts/agentbox move`
  starts the box relauncher with `tmux run-shell -b`. The command may not travel as TEXT in
  the tmux argument: tmux expands `#{…}`, `#S`, `#T` and the rest in it after the quoting, so a
  format value holding a quote — a server option, a window title any program can set — ended
  the quoted word and the rest ran in tmux's own unmarked shell (round 6); and doubling every
  `#` was no transport either, since a boxed caller passes through two wrappers and tmux leaves
  `##` alone before `[`, which broke `${#v}`, `${v#*/}` and a literal `#[fg=red]` (round 7). A
  path of `[A-Za-z0-9._/-]` holds nothing for tmux or a shell to read, so the command arrives
  byte for byte. Two consequences, both deliberate: **an agent gets no tmux format expansion
  inside `run-shell` and `pipe-pane`** (read the value first with
  `tmux display-message -p '#{pane_id}'`, which this shim does not touch, and pass it), and
  **`$0` in such a command is the path of that file**, not the shell's name. A marker value
  holding whitespace, a quote or a `#` cannot travel in that one argument, so it refuses the
  call instead of marking it in part.
- **A boxed caller passes through two wrappers**, this shim and the relay, and exactly one may
  rewrite the command: the relay, which runs where the tmux server and its filesystem are. So
  when a call is bound for `AGENTBOX_TMUX_GUARD_SOCK`, this shim leaves `run-shell` and
  `pipe-pane` alone; the relay wraps them with the two markers it can know, `AGENTBOX_SESSION`
  and `AGENT_SHIM_AGENT=1`.
- **A command that hands the SERVER a tmux command** to run later or in another session —
  `if-shell`, `bind-key`, `set-hook`, `confirm-before`, `command-prompt`, `display-menu`,
  `run-shell -C` — is REFUSED for an agent. The environment of an if-shell condition is not the
  environment of its branch, so there is nothing to mark, and a binding or a hook an agent
  installs outlives it. The person's own bindings live in `.tmux.conf`, which the server reads
  itself; `scripts/tmux-urls` and `scripts/tmux-omp-ids` reach `display-menu` from those
  bindings, in the server's own environment, so they are not an agent's calls and are not
  refused.
- `scripts/agentbox-tmux-guard` does all three with `AGENTBOX_SESSION=<box>`, so a pane or a
  command a box starts on the host server is marked, or refused, whatever shim the box runs.
- `.tmux.conf` adds `SSH_TTY` to `update-environment`, beside the `SSH_CONNECTION` tmux carries
  by default, so the windows a person opens after reattaching over ssh stay the person's. A
  server started before that line, or under another config, carries `SSH_CONNECTION` without
  `SSH_TTY` into those windows and they read as a non-interactive ssh command: the safe
  direction, with `AGENT_SHIM_PERSON=1` as the escape.

`shims/tests/test_shim_agent_context.py` (`TmuxPanes`, `TmuxMarkedArgv`) drives a real server
and the shim's own argv through each case, and `scripts/tests/test_agentbox_tmux_guard.py` the
relay.

**These are guardrails against an accident, not a sandbox**, and two gaps are accepted and
known (dispatch://INFRA-1538, rounds 4 and 5):

- `/usr/bin/tmux` run directly passes no marker. Only the shim on PATH adds them, and an agent
  that reaches past its own PATH is past every shim here, not only this one.
- `send-keys` into a shell that is already running carries nothing: the command runs as that
  shell's owner, with that shell's environment. Typed into a MARKED shell it stays an agent's;
  typed into the person's it is the person's. The relay allows `send-keys` on purpose — a lead
  steers other sessions with it (`agentbox/AGENTS.md`) — so this is a deliberate limit, not an
  oversight. Anything that must be an agent's gets its own pane (`new-window -d`), which is
  marked.

Each shim prints one stderr line when the SSH rule, and not a marker, made the decision, naming
the escape. `az` and `gcloud` name the identity the call ENDED on, never one they have not
selected:

- `gcloud` resolves the final configuration and account in main's own order — `--configuration`,
  then `GCLOUD_ACCOUNT`, then `CLOUDSDK_ACTIVE_CONFIG_NAME`, then `active_config`; and
  `--account`, then `CLOUDSDK_CORE_ACCOUNT`, then that configuration's `account` — and reports
  impersonation from the flag, the environment or the configuration's own
  `[auth] impersonate_service_account`, as the SDK resolves a property
  (`core/properties.py:5487-5527`). A call that chose nothing gets configuration `box`; one that
  chose a configuration, a `--cred-file` or an account keeps its choice (a person's own login
  named that way is refused earlier, by the agent-session account guard); and where this machine
  has no `box` configuration and the one in force holds a person's login, the call is refused.
- **A `--flags-file` in an agent session is refused outright, and nothing in it is parsed.** It
  is argv gcloud has not expanded yet: the SDK loads the file's YAML — a mapping, a list of
  mappings, a JSON one-liner — expands it IN PLACE, in argument order, before it resolves
  `--account`, `--configuration`, `--cred-file` and impersonation, and follows a flags file a
  flags file names (`calliope/cli.py:92-108,125-130,985`). Three rounds of review found a
  reader here missing one valid shape after another, each of which ran as the person while the
  shim named the machine identity, so there is no reader: the refusal is one line and says to
  pass the flags on the command line. A person's own session is unchanged.
- **The one exception to the box selection:** a command whose change outlives it — `auth login`,
  `auth revoke`, `auth application-default …`, `config set`/`unset`/`configurations`, `init`, or
  a path an unknown flag makes ambiguous (`persists_config_change`) — keeps the configuration it
  would have used instead of being moved to `box`, because selecting `box` for it would write or
  empty the machine identity's own state, or sign a person's login into it. The notice says so
  and names that configuration.

`gh`, `git`, `jj` and `secrets` name the rules the call now runs under.

There is no exception for `gh` on a machine whose own git config names `gh-app.host-profile`
(sami-devbox, `installers/gh-app-host.sh`). The host route stays what it always was — a person's
own shell and the machine's own units on a machine with no person's GitHub login — but an SSH
call takes the AGENT route, with every agent safeguard: the public-text leak check, the refusal
of `gh auth` verbs, the agent `GH_CONFIG_DIR`, and the requirement to name the target repository.
The first draft kept the host route for such a call, which lifted it out of all four. It needed
no exception: `gh-app-token` registers its own broker session (`secret-run --register`), and the
profile `installers/gh-app-host.sh` writes is `agent`, so both routes mint the same App token
(`shims/tests/test_gh_host_credential.py`, `test_gh_host_token.py`, `test_gh_unrouted.py`).

The copies are kept identical by hand — `shims/` has no sourced library, and a shim that sources
one would be a second file every call must find — so `shims/tests/test_shim_agent_context.py`
compares them byte for byte and drives the whole matrix; each shim's own test file carries its
end-to-end case. A test that means a person must drop `SSH_CONNECTION` from the environment it
inherits, or set `SSH_TTY` beside it, and name its own stdin either way
(`shims/tests/stdin_cases.py`); a test fixture that commits must bring its own `HOME`, git
configuration and author, or whoever runs the suite decides the case.

## Adding to a shim

A shim composes with everything else that wraps its binary or already does what it adds.
Before adding a side effect or a binary lookup, search for the callers that already do the
same thing and for every other wrapper of the same binary, not only for the callers you mean
to protect: a lock the shim adds can collide with a caller's own lock, and a lookup can find a
pass-through wrapper that hands the call straight back to the shim. A shim that resolves its
real binary takes the first match past its own PATH entry, never the first from the top, and
a pass-through wrapper hands the next binary the PATH tail past its own entry, so nothing
below it can find the wrapper again. `shims/gh` still searches from the top, which is safe
only while nothing puts a gh wrapper above it. `shims/gcloud` does too, under the same
condition, and its routing tests depend on it: they put a stub `gcloud` above this dir and
expect the shim to hand the call to the stub. It skips a copy of itself (same bytes, or its
marker line), since two copies on PATH called each other until an agent box's 8192 processes
were used up on 2026-11-03 (`GcloudShimCopiesOnPath`). `shims/jj` and `shims/tmux` also search from the
top, and hand the binary they find the whole PATH: jj's own `git` calls must still reach
`shims/git`, and a tmux server started through the shim gives its PATH to every pane. The first
jj or tmux they find is usually mise's shim, which hands the call back to the first one on PATH
whenever mise has no version of it for the directory (a relocated `HOME` or `XDG_CONFIG_HOME`
hides mise's global config). So `DOTFILES_JJ_SHIM` and `DOTFILES_TMUX_SHIM` name every binary
the shim has handed the call to in that pid, and a re-entry in the same pid skips each one (and
every path to the same file), taking the next one on PATH or failing with exit 127 instead of
looping.

## How `shims/omp` resolves omp

`shims/omp` runs `mise env -s bash` first. A session keeps the PATH it started with, and that
PATH can name the install dir of an older pin; mise swaps it for the current pin's dir, so a
stale PATH still starts the pinned omp. Then `scripts/agent-session` takes the first `omp` on
PATH that is not a wrapper: it skips mise's shims dir (`~/.mise/shims`), whose `omp` hands the
call back to PATH when mise has no version for the directory, and every `omp` whose real path is
in a `shims/` directory (this shim, another checkout's copy). A local build on PATH ahead of
mise's shims dir wins: `mise env` keeps the entries ahead of that dir ahead of the tool dirs it
adds, and puts its tool dirs first on a PATH without it; with the pin not installed and no local build, the launch fails with
exit 127 instead of looping back into the shim.
