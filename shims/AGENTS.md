# shims

PATH-priority wrappers: each file here shadows the binary of the same name and adds behavior
before handing the call to the real one.

## What each shim adds

- **`gh`** sources the auth token. In an agent session (omp, agentbox, or anything launched
  with the gh-app routing include) it routes every call to the repo owner's GitHub App; an
  owner with no App installation (METR, can1357, every upstream we fork) gets the upstream
  token from the secrets store (`gh-app.agent.fallback-secret`, `GH_PUBLIC_REPO_PAT`). No
  agent process reaches the user's own login, through this shim or past it: gh runs with
  `GH_CONFIG_DIR` at `scripts/gh-agent-config`'s directory, whose placeholder login stands
  where the user's would be, so gh never reads his token from `hosts.yml` or the keyring;
  `scripts/agent-session` and `omp/extensions/session-env.ts` export that directory for the
  whole session (and omp's eval kernels, through `eval.envPassthrough`), and `mise.toml`'s
  `_.path` puts this dir first in every PATH mise builds, since mise's python3 shim put mise's
  gh ahead of it in omp's Python eval kernel, which approved a pull request as the user on
  2026-11-02. `gh auth` verbs other than `status` are refused, and a call GitHub refuses for
  the App (a review of the App's own pull request, a permission its installation lacks) ends
  with a `gh shim: REFUSED` block naming the call, the identity, why, and the route: where
  the repository needs an approving review (agent-infra's main does), a session that did not
  author the pull request posts the review as the legion-reviewer App with
  `scripts/post-review <pr> <head sha> APPROVE|REQUEST_CHANGES <body-file> <owner/repo>`,
  since a comment does not count as an approval; elsewhere a verdict goes out as an App
  comment beginning `VERDICT: <verdict> at <head sha>`; anything
  else stops and goes to the lead (`shims/tests/test_gh_user_login.py`). Outside an agent
  session, `gh auth git-credential` (git's helper in `~/.gitconfig`) goes to the user's login,
  except where the machine's own git config file, `~/.config/git/config` (not the shared
  `~/.gitconfig`, not a repository's), names `gh-app.host-profile` (a machine with no person's
  login, sami-devbox: `installers/gh-app-host.sh`): there it answers through
  `gh-app-token <profile>` as an agent's does (`shims/tests/test_gh_host_credential.py`).
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
  The real gh is the first gh past this dir on PATH. When that is mise's own shim, the gh pinned
  exactly in mise's global config runs straight from its install dir, as `shims/knives` runs
  knives: mise's shim is one more process per call, and under load mise hung for minutes
  (2026-11-03). mise's shim runs only when that install is missing
  (`shims/tests/test_gh_mise_pin.py`).
- **`jj`** locks the git worktree of every workspace `jj workspace add` creates, and in a
  checkout that sets `git config omp.sessionTrailer false` (the backing git repository's config)
  runs every command that can write a description with `templates.commit_trailers` empty and
  `immutable_heads()` back at `builtin_immutable_heads()`: the session overlay's cross-session
  guard (AGENTC-318) tells a session's own commits by their trailer, so it is off there, and
  another session's unpushed commits in such a repository can be rewritten without
  `--ignore-immutable`. Read-only commands (`log`, `st`, `show`, `diff`, `op`, `bookmark`, `git`,
  …) skip the check.
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
  included, unless the checkout sets `git config omp.sessionTrailer false`
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
  It also gives a `git push` that jj runs the pre-push leak check that a git push from the same
  checkout gets. jj runs `git … push --porcelain --no-verify … -- <remote> <sha>:<ref>…` once
  per remote, so no hook runs for it. When the process that ran git is jj and a line of the
  checkout's `git remote -v` passes the hook sweeper's `git remote -v` test (`sweep-hooks.sh`
  beside the check: `(theorem-labs/(<names>)|<others>)(\.git)?/?[[:space:]]`, letter case
  ignored, for the sweeper's six repositories, every `<name>` whose
  `~/.cache/public-leak-check/theorem-labs_<name>` holds exactly `public`, and its `others`,
  sjawhar/dotfiles), the shim drops `--no-verify` and adds a last
  `-c core.hooksPath=jj-push-hooks/`. The sweeper also skips a checkout whose config file names
  none of these as written; the shim does not, so it can check a push the sweeper left without a
  hook. Its `pre-push` makes the installed hook's call, `<check> <remote> <url>`, with the URL
  git is pushing to and the update lines git is about to send; the check, found the way the hook
  finds it, alone decides, for every target. Every other hook name there links to
  `jj-push-hooks/hook`, which takes the shim's `-c` back out of `GIT_CONFIG_PARAMETERS` and runs
  the hook git would have run without it, so the override changes only which pre-push runs.
  Where the answer depends on the cache and the cache cannot give it, the push is refused unread:
  the cache counts as absent only when `stat` says it does not exist (ENOENT); a directory on the
  way, or a symlink's target, that cannot be searched, a cache that cannot be read, or a deciding
  entry that is empty, unreadable, not a regular file or holds another word refuses. Each refusal
  line starts with `error: `, and the shim leaves out the one "failed to push some refs" line git
  writes right after the gate's refusal, so jj shows the commit and the matched text even under
  `--quiet`; another destination's failure keeps its line. Not seen, as `git push --no-verify` is
  not: a jj told to run another git (`git.executable-path`) or copied under another name, a
  checkout of a public repository outside the six that has no cache entry (a machine or box
  without the cache), and a remote changed between the shim's decision and git reading its
  config. `tests/test_jj_push_leak_gate.py` is the contract; it runs the sweeper's own code, as
  it is at that run, on the same checkouts, and fails when the sweeper's `git remote -v` test
  and the shim's differ.
- **`omp`** applies mise's environment, the committed config overlay, mnemopi's recall weights,
  a `PI_SHELL_PREFIX` that turns errexit, nounset and pipefail off before each bash call (so
  one call's `set -euo pipefail` does not break later calls in the persistent shell) and a named
  profile's links, then execs omp through `scripts/agent-session` (below). It sets no
  Envoy, Dispatch or provider-key variable: the session's plugin reads its `envoy.json`, and keys
  come from the profile's `models.yml` (`omp/AGENTS.md`). Each session runs in a transient
  systemd user scope with a hard memory cap and swap off: `OMP_MEMORY_MAX=<size>`, 24G by default
  (dispatch://INFRA-898; the largest of 44 sessions measured on 2026-10-31 held 17.8 GiB), a
  caller's value clamped at 96G. The shim waits for the session instead of exec'ing, and says on
  the pane when the cap was what killed it. A session inside such a scope exports
  `OMP_MEMORY_SCOPE`, so an omp it starts takes no default and shares the parent's scope. One
  session reached 138.0 GiB on 2026-10-26 and the box-wide OOM
  killer took two unrelated pods with it; inside a scope only that session dies. `MemoryHigh` is
  deliberately not set: it throttles a session into reclaim instead of killing it, which looks
  like a lane that is thinking.
  Inside an agent box (`/etc/agentbox-identity`) there is no host systemd to accept a scope and
  none is needed, since the box cgroup is already the ceiling: the shim says that in one line
  instead of reporting a refusal the agent cannot act on, which was the first line of every
  boxed launch until dispatch://INFRA-702.
  A named profile whose `agent/models.overlay.yml` exists is a shared profile: the shim links
  all of the default's state into it and rebuilds its `models.yml` as the default's merged with the
  overlay, on every launch, under a 30 s lock; a real entry on both sides stops the launch
  (`omp/AGENTS.md`, "A shared profile").
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
  10 s exits with an error; `knives gh` enters twice by design, through `shims/gh`.
- **`ssh-keygen`** drops `SSH_AUTH_SOCK` for `-Y`, the operations git and jj sign and verify commits
  with: the signing key is a file, and ssh-keygen asks the agent first, so a stuck agent socket
  hung every signed commit. It execs the first `ssh-keygen` past its own PATH entry.
- **`secrets`** refuses every agent request for a human-tier key
  (exit 77), and names the broker command (`secret-run`) or, in a session the broker does not
  serve, the `omp --resume` line that relaunches it as one. secretsd's only prompt for such a key
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
- **`ctx`** sets `CTX_SERVER` to the team memory graph's server when the caller has none, so a
  session, pane or unit whose environment predates `.bashrc`'s line reaches the server too; an
  empty `CTX_SERVER` is kept and means a local tree (`CTX_SERVER= ctx --memory <tree> …`). It execs
  the mise pin (`"github:sjawhar/ctx-memory"` in `mise.toml`), found with `mise which -C /`, as
  `shims/knives` finds knives, so mise's own shim never hands the call back to it.
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
  (`GCLOUD_LOCK_RETRY_SECONDS`), 0.2-6 s apart with jitter, and a repeat attempt first takes one
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
expect the shim to hand the call to the stub. `shims/jj` and `shims/tmux` also search from the
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
