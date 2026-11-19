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
  repository requires an approval your own identity cannot give, ask INFRA's Legion controller
  which running Legion reviewer reads it and send that reviewer the pull request and its head,
  asking for no review issue (dispatch://INFRA-1481, R1 and R3); elsewhere a verdict goes out as an App
  comment beginning `VERDICT: <verdict> at <head sha>`. A post (`issue create`, `issue comment`,
  `pr comment`, `pr create`) Sami instructed in a Dispatch ask he answered runs again with
  `GH_SHIM_APPROVAL=dispatch://<KEY>/ask/<id>`, which `scripts/gh-approved-post` checks: his
  approving answer, the repository named in the draft, the title as the draft's `Title:` line, the
  body as the text between two `---` lines of it (compared with `\r\n` read as `\n`, without each
  line's trailing whitespace or the leading and trailing blank lines), and no flag but `-R`/`--repo`,
  `-t`/`--title` and one of `-b`/`--body` or `-F`/`--body-file` (gh posts the file over `--body`
  whatever their order, so both are refused) and, for a pull request, `--head` and `-B`/`--base`,
  with the head `<owner>:<branch>`, a branch of one of his own forks, whose owners are a fixed list
  beside `APPROVER_LOGIN` in the script (`APPROVER_FORK_OWNERS`): `sjawhar`, and `cybermonklabs`,
  where his forks moved (on 2026-11-17 `gh api repos/sjawhar/oh-my-pi` answered
  `cybermonklabs/oh-my-pi`, and the gate refused the head to post,
  `cybermonklabs:upstream/fix-hindsight-recall-cooldown`). Push the branch first: both owners
  route to the App. A pull request's `-H` is refused: knives 3.2.3 reads only `--head`
  (`has_head_flag`, `src/commands/gh.rs`), and in a jj checkout it adds `--head <bookmark>`,
  which gh takes over `-H`. An ask Sami answered by approving a KIND of post, with no fixed text
  (dispatch://INFRA-3412), is listed by its id in the script's `KIND_APPROVALS`, with the
  repository and the verbs his answer is read as approving, the option he selected, the name his
  ask or answer gives the repository and the words that say so, which the check finds there:
  dispatch://BET-422/ask/48d87cd6-f3d0-44d5-9a79-30c8b38f28d0 ("Just open an issue and a PR. …
  anything going to the Rock repo") is `gh issue create` and `gh pr create` on `rocq-prover/rocq`,
  and dispatch://OSSV-142/ask/9a66d8e5-92ad-429b-8568-1af4d7ad5a53 ("except for messages to Oh My
  Pie; those can go out", under the option "Person for every outside message", which approves
  nothing by itself) is all four verbs on `can1357/oh-my-pi`. A program cannot read "the Rock
  repo" or "Oh My Pie" as a repository, so each
  entry is a reviewed change to the script. The entry is chosen by the cited id (or a short id of
  it), never by Dispatch's text, and Dispatch's own ask id must be the entry's. A citation of a
  listed ask passes for those verbs on that repository only, and every refusal of it names what the
  answer approves. It needs no draft;
  it needs `GH_SHIM_VERDICT=dispatch://<KEY>/comment/<id>`, the fresh reviewer's verdict on record
  (his three steps end in that reviewer's cut): a Dispatch comment whose first line is exactly
  `VERDICT: APPROVE at sha256:<sha256sum of the body file>` (of a `--body`, its UTF-8 bytes), with
  a `Title: <title>` line for an issue or a pull request, written by a session that is none of the
  call's (`OMP_SESSION_ID`, `DISPATCH_SESSION_ID`, `PI_SESSION_ID`, `ENVOY_SESSION_ID`, compared
  without letter case). A subagent of the posting session writes to Dispatch as that session, so
  its verdict is refused: the reviewer is another session, such as a Legion reviewer. A text that
  changes after the verdict needs a new verdict, not a new ask. An ask that is not listed gets
  only the word-for-word check. For every citation, his answer's text ends before Dispatch's
  `- Reference:` line and the blocks it lists under the answer, which other sessions wrote, and
  an ask whose `dispatch read` holds more than one `Ask id:` line is refused: a question or a
  reply that holds an answer block cannot stand in for his answer. The shim copies a cited post's
  body file (`-F`/`--body-file`) once, before the check, into a private file under the read cache
  root's `bodies/` (directory 0700, file 0600), and the gate checks that copy and gh posts it, so a
  file changed after the check is not what goes out. The copy goes when the call ends; one left by
  a pass that ran gh in place goes at the next cited post an hour on. In an agent session a write
  to an owner without the App, which would go out on his upstream token, is refused without such a
  citation (dispatch://INFRA-2006, `shims/tests/test_gh_fallback_write_gate.py`). The citation
  is checked on the outermost pass only; the later passes of the same call (the stderr-keeping
  re-run, the read cache's re-entry, knives' own queries) get its pass as `GH_SHIM_APPROVED_CALL`,
  the SHA-256 of the citation and the exact argv, which counts only where both match and which an
  outermost call drops unread. Until 2026-11-16 nothing carried it, so that gate refused every
  approved post to such an owner after its `allowed` verdict (dispatch://INFRA-3904,
  `shims/tests/test_gh_approved_post.py`). As with the agent markers below, this is a guardrail
  against an accident, not a sandbox: a caller that sets `GH_SHIM_INNER` and computes the digest
  gets past the gate, as one that sets `GH_TOKEN` does. Every
  call GitHub refuses for the App except a review also gets the admin route: a GitHub admin
  action that work Sami already asked for needs (an App permission, an admin merge, a
  repository or branch setting) is done by the agent itself with Sami's admin token from the
  credential broker, never asked of him (he said so five times, 2026-10-25 to 2026-11-03, last
  on dispatch://OSSV-300/ask/dc4e1c38-bd15-4999-9ef5-4c6540a2ce9a). An approval of a pull
  request is not an admin action: no approval uses a person's token, and a code owner's
  approval stays that person's (dispatch://INFRA-1481, R3).
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
  search budget read as core with thousands left. Both blocks read the call's stderr only when the
  call reached gh, which its inner pass marks just before it hands the call over: a refusal before gh
  ran (the public text gate, the leak-term check) prints the caller's own text, which may say
  `HTTP 403` (dispatch://INFRA-4383). Outside an agent
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
  caller set is cleared first), and gives gh the same bytes only when every check passes. The
  shim's stdin buffer has no pathname after it is opened; killing the shim leaves no named shim
  stdin buffer (`shims/tests/test_gh_public_text_gate.py`).
  For every caller, agent or person, a call that can carry text then goes to the leak-term check,
  `scripts/leak-terms-check gh -- <argv>`, on the same pass, after that gate: the encrypted list
  that refuses a term in a commit or a push (below), its repository-scoped lists, and the
  `forbidden` lists of knives's fork registry, all read by the one matcher ("Leak-term list"
  below), refuse a term in a title, a body, a comment, a review, release notes or an API field
  before gh sends it, with exit 2 and one line per match naming the command, the target, the field
  and the line in it, the text that matched as it is written, the list that holds the term and the
  line it is on ("Leak-term list" below). On 2026-11-17 an agent's `gh pr edit` put a listed
  term into a public pull request's body, and the fork's pull-request bodies took terms of the
  registry's oh-my-pi list; GitHub keeps every body revision in the edit history, which only an
  admin can delete, so refusing before the post is the only fix (dispatch://INFRA-4102). The
  shim hands the check every write `gh_shim_is_write` names (the
  command and its verb found past any option before them, as cobra finds them, so `gh -R o/r pr
  comment` is a `pr comment`, and the options of `gh api` read as pflag reads them: a cluster such as
  `-iXPOST` or `-if k=v`, and a word an option takes, such as the template of `-t -XGET`), every
  first word that is not a gh command, and every command under which gh's `config.yml` holds an
  alias (`gh alias set 'pr say' 'pr comment'`), since a gh alias is read as what it expands to; a
  read never starts it. The check reads gh's
  arguments as gh does, with each command's options from `gh <command> --help` at the pinned gh
  (`GH_TEXT_COMMANDS`; `shims/tests/test_gh_leak_terms.py` fails when the installed gh's options
  differ), and scans every argument and option value whole, but those gh keeps to itself (the
  `--jq`, `--template` and `--cache` of `gh api`, the `--jq`, `--template` and `--format` of `gh
  project`), and for a file gh reads (`--body-file`, `--notes-file`, `--from-file`, `--recover`,
  `--input`, `-F key=@file`, a gist's files) what the file holds, never its path. The repository a
  call names (`-R`, a URL, a `gh api` endpoint) is
  text too, under one rule: a match does not count for a target whose visibility was read as
  public when it lies wholly inside an occurrence, in the same value, of that repository's own
  name, its `owner/name` or its owner alone, in any letter case and as a whole word by GitHub's
  login characters (no letter, digit, `_` or `-` on either side, and after `owner/name` no `.`
  either, but a `.git`). That name is public already, so a term inside it sends nothing new; a
  term anywhere else (`INFRA-1`, `dispatch://`, `theorem-labs-internal`) is still refused, and so
  is one for a target whose visibility cannot be read. Until 2026-11-19 every write to a public
  repository whose own name holds a listed term was refused (theorem-labs/ygo under the knives
  ygo-fork list, dispatch://INFRA-3365); that day's first fix left the name out of each spelling
  the check knew, and five test rounds and a review each found one more. A repository the call
  makes public is not public yet, so the call publishes its name, which is read with no such
  exception: `repo edit --visibility public`, `repo
  create` but with `--private` or `--internal` (given no name, gh names the repository after the
  directory `--source` names, which is read as `--source name`), and a `gh api` call to a
  repository's own endpoint `repos/O/R` whose field or `--input` body sets `visibility` to public
  or `private` to false (in any letter case, and without the whitespace around the value, such as
  the line break `-F visibility=@file` sends), or whose `--input` cannot be read; each is read as
  public. A name that no text of the call gives whole is read as a text of its own, `the name of
  the repository the call makes public (remote origin)` or `(GH_REPO)`: the checkout's repository
  that a `repo edit` with no repository argument edits. A placeholder endpoint
  (`repos/{owner}/{repo}`, `repos/:owner/R`, `repos/O/{repo}`) gives the name whole once filled as
  gh fills it (below), and one gh fills from a base repository whose name the check cannot read is
  refused as unreadable. The second test round of 2026-11-19 found a private name that `repo edit
  --visibility public` published unread, and its third the same name published by a PATCH of
  `repos/{owner}/{repo}`. gh fills the placeholders `gh api --help` lists, `{owner}`, `{repo}` and
  `{branch}`, each also as `:owner`, `:repo` and `:branch` (its own pattern, `pkg/cmd/api/api.go`),
  in a `gh api` endpoint and in each `-F` value but a file's (`@path`): the owner and the name from
  the base repository, `GH_REPO` when it is set, else the directory's remote (the one `gh repo
  set-default` chose, else each), and the branch with the current branch, which `git symbolic-ref
  --quiet HEAD` names. The check reads each as gh sends it, once for each base repository:
  `repos/{owner}/R` is the base owner's R, `orgs/{owner}/repos` names no repository, `-F
  name={repo}` sends the base repository's name, and `-F ref=refs/heads/{branch}` or
  `repos/{owner}/{repo}/git/refs/heads/{branch}` the branch's (until the fourth test round of
  2026-11-19 `{branch}` was read as written, so a branch whose name holds a listed term went to a
  public repository unread). A base repository whose name it cannot read leaves the endpoint's
  repository unknown, read as public, and the value read as written. Each filling is tied to its
  base: gh sends it only when it takes that base, so it counts only for the targets the call
  reaches with that base and those that depend on none (below). The check reads the base
  repositories (`git remote -v`, `git config --get-regexp`) at most once per call, and only for a
  text that holds a base placeholder, a `repo edit` that names no repository and makes it public,
  or, once a term is found, where a call that names no repository goes: until review round 2 of
  2026-11-19 it read them for every call, which made a clean `pr comment` 43 to 72 ms slower.
  gh sends nothing for a call
  whose branch it cannot fill, with `GH_REPO` set, on a detached HEAD (as a jj checkout that shares
  its git directory usually has) or outside a git repository: gh refuses it, and the check passes
  it. The list is gh's (`GH_PLACEHOLDERS`): `shims/tests/test_gh_leak_terms.py` fails when the list
  the installed gh's help gives differs.
  Which arguments name the repository comes from each command's usage in
  `gh <command> --help`
  (`GH_REPOSITORY_ARGUMENT`, `GH_URL_ARGUMENT`; the same test fails when they differ). A URL in any
  other argument, such as the description `gh agent-task create` sends, names no repository: until
  2026-11-19 it was read as where the call goes, so a description that began with a private
  repository's URL went to a public checkout's repository unread. A URL's host is read as gh
  2.98.0 reads it: gh sends the call to github.com for each host go-gh's `auth.NormalizeHostname`
  makes github.com, the host in any letter case and any host ending in `.github.com` but
  `garage.github.com`, so `https://foo.github.com/O/R/pull/1` names github.com's O/R; a URL on any
  other host names a repository there, read as public, and so does a repository argument on one
  (`repo edit ghe.example.com/o/r`, which until review round 2 of 2026-11-19 was read as a
  github.com repository whose visibility could not be read). `GH_HOST` and `gh api --hostname` are
  read the same way. Until 2026-11-19 only github.com and www.github.com named a repository, so a
  comment on such a URL of a public repository was judged by a private checkout's remote and went
  out unread (dispatch://INFRA-3365, review finding 1). Each host is decided where its value is
  read: `-R`, `--branch-repo`, `GH_REPO` and a repository argument as gh reads `[HOST/]OWNER/REPO`
  or a URL, a URL argument by its host, and any other git URL (`git@host:o/r`, `ssh://…`) as
  github.com's, which `scripts/github-repo-visibility` reads as ssh does, a URL it does not read as
  GitHub's then read as public. A remote and an endpoint's repository are
  github.com's: the directory's remotes are kept only when that helper reads them as GitHub's,
  resolving an ssh host alias with `ssh -G` as gh does (`ssh.NewTranslator`), so `git@gh-work:o/r.git`
  and `gh-work:o/r.git`, gh-work an alias of github.com, are github.com's o/r (until review round 3
  of 2026-11-19 a second parser read the first as a repository on gh-work, read as public, and the
  second as o/r).
  Each body is read as its endpoint stores it, and each value on its own,
  so two values never make one match (dispatch://INFRA-4383). A release asset's body (`gh release
  upload`, or `gh api` to a `repos/O/R/releases/N/assets` path or to an address on
  uploads.github.com) is its bytes, read as written and never as JSON, since GitHub stores them so:
  read as JSON, an asset's `\u002f` once decoded into the `/` of a token its repository allows and
  sent the scoped term inside it. A body that parses as JSON (`--input`, `workflow run --json`) is
  read as each string and member name, decoded, by its JSON path (`--input body.json
  .comments[0].body line 3`); a field's key is read apart from its value; and a GraphQL document
  that parses (the `query` field, or a JSON body's `.query`, of a call to GitHub's GraphQL API, read
  by the spec's grammar of an executable document: operations, fragments, selections, arguments,
  values and directives, every string closed and no control character) is read as each of its
  strings, decoded by GraphQL's rules, block strings included
  (`--raw-field query string 2`), so a `\u` escape in a GraphQL string inside JSON is the letter
  GitHub stores (dispatch://BET-727), while a comment, which nobody stores, is not read. A `gh api`
  call goes to GitHub's GraphQL API when gh resolves its endpoint (`pkg/cmd/api/http.go`) to the
  path `/graphql` on api.github.com, in any letter case, with any query string or fragment:
  `graphql`, `/graphql`, `graphql?x=1`, `graphql#x` and `https://api.github.com/graphql?x=1`, each
  of which GitHub ran as GraphQL (measured 2026-11-19, while `graphql/` and `//graphql` answered
  404; `GRAPHQL` answered 502 and is read as GraphQL all the same, erring toward refusing; the
  comment on `GH_GRAPHQL_PATH` in `scripts/leak-terms-check` lists every spelling tried). Until
  review round 6 of 2026-11-19 only `graphql` was GraphQL, so a mutation to `/graphql` was read as
  written, and a term spelled with a GraphQL `\u` escape went out. Every other text is read as
  written: a body's `\u0041` is those six characters on the page. A body that does
  not parse as JSON, and a document that does not parse, are read both ways, as written and, when
  they hold a backslash, with their JSON escapes decoded: the check cannot tell what GitHub stores
  there, and a refusal across two values beats a miss. An allowed token ("Leak-term list" below)
  counts only where the text each reading reads holds it, so a decode the endpoint does not make
  cannot make one: an asset's bytes are never decoded, and in a body read both ways the as-written
  reading keeps its own matches.
  Within a value, the text is read as GitHub renders prose, a
  paragraph at a time (as a commit message is, "Leak-term list" below), so a term of several words
  that a wrapped line or a no-break space splits is the term on the page. Each reading is read once
  more with CommonMark's escapes undone, as GitHub renders them: a backslash before an ASCII
  punctuation character and an entity or numeric character reference, so `INFRA\-1`, `INFRA&#45;1`
  and `Quillfeather&nbsp;Lane 9` are the terms on the page (until review round 2 of 2026-11-19 they
  went to a public repository unread); such a match is shown only when the as-written reading's
  match of the same term does not count. Inline markup that splits a term (`IN*FRA*-1`) is not
  undone, and a look-alike character is not the character it looks like (`&hyphen;` is U+2010, not
  `-`). A file that is the call's
  stdin while that is a pipe or a
  terminal (`/dev/stdin`, `/dev/fd/0`) is read from the stdin buffer, as `-` is; any other pipe, a
  socket or a device but `/dev/null` is not read at all, since reading a pipe takes the text gh
  reads after the check (until 2026-11-17 a `--body-file <(…)` went out empty), so such a text is
  refused for a target not confirmed private and left to gh for a private one.
  It also reads the text gh takes from the directory's git: for `release create --notes-from-tag`
  the tag's message (`git tag --list TAG --format=%(contents)`, as gh reads it), and for `pr create
  --fill`, `--fill-first` or `--fill-verbose` the message of each commit of the head branch (`--head`,
  else the current branch) that a base gh may list it against does not hold, and the head branch's
  name, which gh makes the title of a pull request of more than one commit. The base is `--base`,
  else the branch's `gh-merge-base`, else the default branch gh asks GitHub for, read here as each
  remote's `HEAD`; gh takes it on the base repository's remote, else as a local branch, so each name
  counts on every remote and locally, and a call none of whose bases exists here cannot be read.
  Stdin (`-`, `@-`, `workflow run --json`) comes from the buffer above, through its own
  `DOTFILES_LEAK_TERMS_GH_STDIN=/dev/fd/N` (cleared first, like the gate's) after the check's exit
  7 asks for it, so both checks read the same bytes gh then gets. A `gh api` call counts when it
  sends a body (a method other than GET or HEAD, or gh's default POST for a field or `--input`), and
  a call to GitHub's GraphQL API only when a text holds `mutation` (in an `--input` payload, also as
  a JSON escape spells it) or cannot be read. `pr create --dry-run` sends nothing and is not read.
  Only a found term (or a list it cannot
  decrypt, or a file it cannot read) makes the check read where the text goes: every repository
  the call names (`-R`, a pull-request or issue URL on a host gh sends to github.com, a user or a
  port in it included,
  `repos/OWNER/REPO/…` as a path, its placeholders filled as gh fills them (above), or as an
  address on `api.github.com` or `uploads.github.com`,
  where gh sends a `gh api` endpoint holding `://`, the repository `repo fork`
  forks, whose visibility a fork takes), else
  `GH_REPO` for a command with `-R`/`--repo` (gh reads it for no other, so `repo edit` and `repo
  fork` take the directory's remote; until 2026-11-19 they were read as `GH_REPO`'s), else each
  GitHub remote of the directory (the one `gh repo set-default` chose, else
  all). `issue develop --branch-repo R` makes its branch in R, which is one more target and never
  the call's repository: the issue is in the call's repository (`-R`, else the issue's URL, else
  the base repository), which is always a target, since the issue's page shows the branch by R's
  name. Until review round 3 of 2026-11-19 R was read as the call's repository, so with no `-R`
  the issue's repository was no target, and a term in `--name` went to a public issue, judged by a
  private R alone. A raw GraphQL mutation (a call to GitHub's GraphQL API whose text holds
  `mutation`) is read whole, as one target, `a raw GraphQL mutation, which is read as public`,
  whatever repository or node it names: the check does not read where a mutation goes, and the
  mutation's text is judged with no own-name exemption. A GraphQL call one of whose texts cannot be
  read may be a mutation, so its one target is `a GraphQL call whose text could not be read, read
  as public`. The cost: a raw GraphQL mutation whose text holds a listed term is
  refused even to a private repository, and so is one that names a public repository whose own name
  holds a term (createCommitOnBranch's `repositoryNameWithOwner: "theorem-labs/ygo"` under the
  knives ygo-fork list). Its one target counts as any repository, so every scoped list and every
  knives list counts too: on a machine with the knives registry copy, a mutation that names any
  theorem-labs repository (createCommitOnBranch to `theorem-labs/agent-infra` with a clean
  headline, measured 2026-11-19) is refused for `theorem-labs`. A refusal for a term names the gh
  subcommand or REST route to use instead, judged by its repository, from `GH_GRAPHQL_ROUTES` in
  `scripts/leak-terms-check` for each mutation its document names (for a thread reply, `gh api
  repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies -f body=…`), and for a document
  that names none, that a gh subcommand or the REST API is. Until review round 5 of 2026-11-19 the
  check read each top-level field of a mutation, its node ids (with one `nodes(ids:)` read through
  this shim) and the repositories it named, to find its targets, and each test round found one
  more way that reading and GitHub's differed
  (dispatch://INFRA-3365). Each target's visibility is read through
  `scripts/github-repo-visibility` (the
  leak check's per-repository
  cache, else one gh read, whose answer it caches). A text counts only for the targets it goes to:
  a text gh fills from a base repository for those the call reaches with that base and those that
  depend on none; the value of `-R` for the repository it names, as that repository's address; the
  value of `--branch-repo` for its own repository and the issue's; any other text for every target;
  and every text for the one target of a call that can reach no other (a gist, a project, another
  host, a new repository, a GraphQL call). Until review round 2 of 2026-11-19 every text
  counted for every target, so in a ygo checkout a reply to `repos/{owner}/{repo}/…` was refused
  for the theorem-labs/ygo remote's filling judged against sjawhar/ygo. A term of the list every
  repository has is refused when any target its text goes to is not confirmed private or internal;
  a term of a scoped list or of the registry only when such a target is one its scope names. A
  visibility it cannot read, a gist, a project, another host (`GH_HOST`, `gh api --hostname`, a
  `gh api` address on any host but those two, or a URL or repository argument on one), a
  GraphQL call, a `gh api` call that makes a repository (`user/repos`, `orgs/O/repos`, or a
  template's `repos/O/R/generate`,
  whatever the template's visibility), a base repository whose name it cannot read, and any other
  endpoint count as public and as any repository. Not checked, because the shim runs before the
  text exists or nothing new is typed: text
  typed into gh's own editor, browser or prompts (`-e`, `-w`, which need a terminal an agent's call
  has not got), the notes `--generate-notes` has GitHub build from pull requests already on it,
  release asset contents (their names and labels are checked), and the values of `secret set`,
  `variable set` and key titles, which only the account or repository admins see. Nor is `issue
  transfer`, by GitHub's rule, quoted from docs.github.com, "Transferring an issue to another
  repository" (read 2026-11-19): "You can only transfer issues between repositories owned by the
  same user or organization account. A private repository issue cannot be transferred to a public
  repository." So a transfer into a public repository moves text that was public already.
  `label clone` copies another repository's labels, which the check could read only by asking
  GitHub for them. Extensions (`gh stack`) call the API themselves.
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
  The real gh is the first gh past the shim's own PATH entry (the first entry whose `gh` is this
  file, through a link too), or, when the shim runs by a path outside PATH, the first from the top;
  a copy of the shim (its `knives-gh-shim` marker) is never the real gh, and a gh above the shim's
  entry is never taken. Until 2026-11-18 it was the first from the top, and a wrapper put above the
  shim that ran it again was taken for the real gh (`DOTFILES_GH_SHIM`, "Adding to a shim" below).
  When that gh is mise's own shim, the gh pinned
  exactly in mise's global config runs straight from its install dir, as `shims/knives` runs
  knives: mise's shim is one more process per call, and under load mise hung for minutes
  (2026-11-03). mise's shim runs only when that install is missing. With no such gh on PATH the
  pinned install runs too, found without HOME ("A moved HOME" below), and with neither the shim
  names PATH, the configuration and the install dirs it read (`shims/tests/test_gh_mise_pin.py`).
  A `gh api` call for a job's log (`…actions/jobs/<id>/logs`) gets
  `--allow-escape-sequences` unless it already carries it: a job log holds terminal escape
  sequences, which gh refuses to print, so the call returned nothing and the caller read no CI
  failure (11,209 such refusals on this fleet in the week to 2026-11-08). A log is text to read,
  not a document to render.
- **`jj`** locks the git worktree of every workspace `jj workspace add` creates, and where
  `scripts/omp-session-trailer` says no for the backing git repository (`git config
  omp.sessionTrailer false` there, or a throwaway repository, as for `git` below, unless every
  `omp-attribution-*.toml` on `JJ_CONFIG` lies under a temporary root too: the overlay of an omp
  session a test started, whose trailer is the test's subject, as in Legion's commit-trailers test)
  runs every command that can write a description with `templates.commit_trailers` empty and
  `immutable_heads()` back at `builtin_immutable_heads()`: the session overlay's cross-session
  guard (AGENTC-318) tells a session's own commits by their trailer, so it is off there, and
  another session's unpushed commits in such a repository can be rewritten without
  `--ignore-immutable`. Read-only commands (`log`, `st`, `show`, `diff`, `op`, `bookmark`, `git`,
  …) skip the check.
  For every caller, the shim resolves aliases before it handles `jj commit`, `ci`, `describe`,
  `desc` or `new`; an alias therefore gets the same check as the command it expands to. An
  unknown option to one of those commands refuses, rather than risk reading the wrong revision.
  `commit` and `ci` scan the working-copy diff, limited to their named paths unless an
  interactive or tool-driven commit selects all of it; `describe` and `desc` scan every selected
  revision (or `@`); and `new` scans the working-copy diff before it creates its new revision.
  Each scans its explicit `-m` or `--message` text, and `describe`/`desc` also scan `--stdin`;
  an unreadable standard input refuses before jj runs, while an empty message or diff does not
  load the encrypted list. With `--editor`, only the edited text is scanned, as jj stores only that.
  Where `commit`, `ci`, `describe` or `desc` opens an editor, the shim replaces `ui.editor` with
  `leak-terms-check jj-editor`; the wrapper runs jj's selected editor with the argv jj would give
  it (a string is split as jj 0.46 splits it: by the shlex 2.0.1 crate's rules when it holds a
  quote, else at each space) and scans the descriptions after jj removes `JJ:` lines and text
  after `JJ: ignore-rest`. One description is read whole. For several, each
  `JJ: describe <12-hex id> -------` section is read on its own; a header whose id the shim did
  not expect, or text before the first header, refuses without printing the header. It rejects
  an editor that is the wrapper itself, and names an editor configuration it cannot read as such;
  its active guard rejects a recursive wrapper run. The
  `--deleted` refusal and trailer handling stay as they were.
  One table of jj 0.46's global options (`jj_global_options`) and one reader (`jj_global_at`)
  serve every place that reads them: the command finder, the repository of the trailer and push
  target, the commit/describe/new parser, the push gate's flags, the `--deleted` refusal and
  `workspace add`; a global's separate value is never read as a flag or a command.
  `DOTFILES_JJ_SHIM_READ_GLOBALS=1` prints the command and repository that reader finds and runs
  nothing (`tests/test_shim_global_options.py` compares it with the real jj).
  Every `jj git` finds its nested subcommand with that same reader. Therefore every
  `jj git <global options> push` first dry-runs its
  resolved destination plan and, for every public or unreadable GitHub URL, scans the outgoing
  commits that destination does not advertise, annotated-tag messages, direct file and folder
  objects the destination does not have and no outgoing commit includes, and each created or
  updated ref name. jj's plan names
  a tag's commit, but jj pushes the backing repository's `refs/tags/<name>`, so the shim reads
  that object and refuses when it differs. This refusal says only that a tag in the plan differs.
  A refusal for an unreadable plan update also omits the update text and its names. This
  encrypted-term check runs for every caller before the shared public leak check, and fails closed
  when it cannot read the plan, destination or a nonempty scan's term list. The shared public
  check remains an agent check, or a person's check where it is installed;
  `DOTFILES_JJ_NO_LEAK_CHECK=1` skips only that public check for a person's one command, never
  the encrypted-term check. In an agent session the variable is ignored. Every destination must
  pass before any actual push. A failed dry-run, unreadable plan or nonzero check refuses; without
  a check, only a destination confirmed private passes in an agent session (outside one, no check
  means no public gate). A push that creates or updates no ref (only deletions, or nothing to change)
  does not load the encrypted list.
  The shim resolves push aliases in jj's startup invocation context: up to eight levels in an
  agent session, up to 64 outside one. A command whose first word is a builtin other than `git`
  needs no alias lookup. Lookup/decoding failures and deeper expansions refuse (a person's
  refusal names `DOTFILES_JJ_NO_LEAK_CHECK=1`), since an alias the shim cannot resolve may be a
  push; outside an agent session a cyclic alias is left to jj, which refuses it itself and runs
  nothing. The checked expanded argv is the actual push argv. Non-GitHub destinations pass
  unchanged. `--quiet` needs a second dry-run with only that rendering flag removed to read the
  plan. The gate does not depend on jj's git executable or git's hooks. Its nested parse also
  makes the agent `--deleted` refusal apply when globals stand between `git` and `push`.
  In an agent session `jj git push` first refuses a push that would create on a remote a bookmark
  whose name carries a person token, by the rule under `git` below. Each bookmark that jj's own
  dry-run plan adds (`add to …`) goes to `scripts/person-branch-guard` before anything is pushed,
  and a refusal carries jj's hint (`jj git push --named <issue-key>=<revision>`). A bookmark the
  plan moves or deletes is not checked, and neither is a tag or a push with `--dry-run`. The
  guard's exit 3 refuses the push and the shim exits 1. On any other non-zero status the shim
  prints `the branch-name check failed (exit <n>); the push goes on.`, and the push goes on. It
  runs after the encrypted-term check above and before the shared public leak check. The `git push` that jj then runs passes through
  `shims/git`, which checks it again.
- **`git`**, in an agent session, refuses a bare `git worktree prune` and locks every
  `git worktree add`, so no box can prune another box's live registration. omp's bash tool
  passes it that command only while `worktree.clone` is off, as `omp/config.yml` sets it:
  on, omp runs the add itself as `omp worktree add`, unlocked (dispatch://INFRA-3990).
  The prune runs
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
  an ordinary alias gets the trailer of the command it expands to (the expansion is described
  below); a shell alias (`!…`), `git am` and `git commit-tree` add nothing. Commits omp writes through its own git
  library never run `git`, so no shim reaches them: omp appends `PI_COMMIT_TRAILERS`, which
  `omp/extensions/session-env.ts` sets to the session's line.
  Before it dispatches a command, the shim finds the command with one table of Git 2.43's global
  options (`git_global_options`) and one reader (`git_read_global_options`), which the alias
  rule, the caller hooks-path check and `worktree remove`'s `-C` base also use;
  `DOTFILES_GIT_SHIM_READ_GLOBALS=1` prints what that reader finds and runs nothing
  (`tests/test_shim_global_options.py` compares it with the real git). The table covers every
  global option
  in `git.c`'s `handle_options`, including separate values for `-C`, `-c`, `--config-env`,
  `--git-dir`, `--work-tree`, `--namespace`, `--shallow-file` and `--attr-source`, and
  attached-only forms such as `--exec-path=<path>` and `--list-cmds=<list>`. A global query or
  unknown option goes to Git unchanged, which reports its own result or error. When that command
  is not a Git builtin and no external `git-<command>` exists in Git's exec path or on `PATH`
  (Git runs that before it reads an alias), the shim reads `alias.<command>` through the real Git
  with those globals, applies Git's quoted `split_cmdline` rules, and repeats for each ordinary
  alias. The expanded arguments are both checked and run. A builtin never starts the Python
  alias decoder. A missing alias and a shell alias (its value starts with `!`) stay with Git; an
  empty, split, malformed or failed decoder result refuses before Git can change anything. An
  alias loop stays with Git and is not expanded by the shim. Every caller's `git commit` gets
  `core.hooksPath=git-hooks/` unless an exact, case-insensitive `core.hooksPath` key appears in a
  separate `-c key=value`, a separate `--config-env key=environment`, or an attached
  `--config-env=key=environment` global. Values of other configuration keys do not exempt it.
  The hook first runs the repository's `pre-commit` or `commit-msg`, with Git's same arguments and
  standard input; only a successful hook is followed by the scan. The pre-commit hook then calls
  `leak-terms-check staged`, which produces the cached diff with an empty attribute-source tree,
  `core.attributesFile=/dev/null`, and `GIT_ATTR_NOSYSTEM=1`, so tree, configured, and system
  attributes cannot hide text. Git has no switch for a non-empty `info/attributes`; then the
  scanner uses `--text`, including NUL-containing bytes. Otherwise Git's own binary detection
  keeps a file containing a NUL byte in its first 8,000 bytes outside the text scan. It reads
  added lines and result-side paths from the staged diff, submodule changes included whatever
  `diff.ignoreSubmodules` says. The commit-message scan decodes Git's effective
  `i18n.commitEncoding` (UTF-8 by default), then applies the effective cleanup mode (an explicit
  `--cleanup=default` resolves as Git resolves it), `core.commentChar`, and scissors handling.
  For `core.commentChar=auto`, the character is taken only from what Git wrote into the prepared
  message: without the editor or the status, Git's own choice from that text; else the character
  of Git's cut lines when Git wrote them (scissors or verbose, a line for each) and every cut line
  in the file has that one character, or the one candidate that starts Git's comment block after
  the message. When that is not certain, every line is read. A comment or text after scissors that
  Git removes is not read; the same text is read when Git keeps it. These commit scans do not
  cover `git commit --no-verify`, a caller's exact hooks-path configuration, `merge`, `pull`,
  `cherry-pick`, `revert`, `rebase`, `am`, `commit-tree`, or Git run by its full path.
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
  message names the token and its source (`USER`). The push that `agentbox move` makes of a moved
  tree's branches to the target machine runs the git past this shim (`move_real_git`): it goes to
  a box over ssh, never to GitHub, so neither this check nor the leak-term gate sees it.
  `tests/test_person_branch_guard.py` is the contract.
  Every `git push` through this shim, for every caller, gets the encrypted-term pre-push gate
  except a push from a checkout that is not guarded to one of its configured remotes. The
  destination is the one Git uses: the first non-option argument, else the last `--repo`, else
  `branch.<current>.pushRemote`, `remote.pushDefault`, `branch.<current>.remote`, `origin`. A URL,
  an `insteadOf` alias and any other name that is not a configured remote take the gate. The shim
  reads the push argv as Git 2.43 parses it (unique abbreviations and `--no-` forms of long
  options, bundled short options, required and optional values, `--` and `--end-of-options`); an
  option it cannot read or an ambiguous abbreviation refuses, and an option left without the value
  it requires at the end goes to Git, which rejects it and pushes nothing. The gate's pre-push scans when the
  URL Git gives it, after `insteadOf`, is a public or unreadable GitHub repository
  (`scripts/github-repo-visibility`). It reads Git's update lines and the destination's live
  advertisement, then finds the commits the updates send that the destination does not hold, and
  each annotated tag object they send that it does not hold (a tag that another sent tag points at
  included). No such commit and no such tag message means no encrypted-list read; otherwise it
  scans the commits' added result-side lines and paths and their messages, read as UTF-8 and with
  replacement objects ignored, and each tag's message, named `tag <name> message line <n>`. The
  outgoing-diff producer applies the same empty attribute tree, configured-attribute override,
  and system-attribute disablement as the commit hook. A non-empty `info/attributes` makes that
  producer use `--text`; its bytes remain safe because messages use a separate NUL-delimited
  producer and diffs use a separate byte stream. The diff stream frames `commit <object-id>` lines
  only outside hunks, whose declared old and new counts are checked before a line can be a
  boundary. Without `info/attributes`, Git's own binary detection still omits NUL-containing file
  bytes. A merge is not read from a diff. Git's combined diff (`--cc`) is wrong for wide octopus
  merges (`combine-diff.c`, Git 2.43 to master): from 33 parents it shows a removed line under the
  wrong parent, so its hunks disagree with their own counts, and at 64 parents it leaves out a line
  that only the merge adds. So the scan reads each merge's files: `git diff-tree --stdin --always`
  against each parent names the files that differ from every parent's version. The scan reads
  each such path, and each line of the merge's file that no parent's file at that path holds, as
  `commit <id> <path>:<line>`. A line the merge took from a parent is scanned in that parent's own
  commit when the push sends it; otherwise the destination already holds it. Those files are text
  as the diff producer reads them, and a file that cannot be read refuses the push (exit 4),
  naming the merge and the file. The scanner refuses combined diff text on its standard input.
  jj runs `git … push --porcelain --no-verify … -- <remote> <sha>:<ref>…` once per remote,
  so no hook runs for that push; the shim directs those pushes through this gate by the same rule.
  The checkout is guarded when a URL `git remote -v` prints (read whole after the tab: a push line
  without its final ` (push)`, a fetch line up to its first ` (fetch)`, since a partial clone's
  filter, which may hold anything, follows that; a remote listed with a blank URL, its name and a
  tab only, has no URL in its config, and git pushes to its name after `url.<base>.insteadOf` and
  `pushInsteadOf` rewrite it, so its URLs are those `git remote get-url --push --all <name>` and
  `git remote get-url --all <name>` print, and a failure of either is unknown) is a GitHub
  repository that is public or whose visibility cannot be read, or has a host part that cannot be
  parsed, as `scripts/github-repo-visibility` reads it: a leading `<transport>::` and `git+`
  stripped, github.com, www.github.com or ssh.github.com (one trailing dot allowed) directly or
  through an ssh alias (`ssh -G [-p PORT]` with the URL's user; an alias is unknown when git would
  run its own ssh command: the checkout's `core.sshCommand`, `GIT_SSH_COMMAND` or `GIT_SSH`), the
  leak check's fresh cache entry, then gh. A private or internal repository, a local path and
  another host are not guarded (dispatch://BET-706).
  `jj-push-hooks/pre-push` makes the installed hook's call, `<check> <remote> <url>`, with the URL
  git is pushing to and the update lines it is about to send; the check alone decides where it
  runs for every target. Every other hook name there links to `jj-push-hooks/hook`, which takes
  the shim's `-c` back out of `GIT_CONFIG_PARAMETERS` and runs the hook git would have run without
  it. A plain `git push` runs the pre-push hook of Git's effective hooks directory
  (`core.hooksPath`, else the common hooks directory). The shared public check reaches it there
  only through the leak-check wrapper (`PUBLIC-LEAK-CHECK-WRAPPER`) or the global hooks dispatcher
  (`~/.local/share/public-leak-check-hooks/global`, whose `dispatch` carries
  `PUBLIC-LEAK-CHECK-DISPATCHER`; `.gitconfig` includes it where step prepush of
  `devbox/sami-devbox-provision` installed it; dispatch://BET-706 R6). Neither reaches a fresh
  clone without the dispatcher, or a repository whose own `core.hooksPath` (husky, the pre-commit
  framework, or an empty value Git reads as `/`) overrides the dispatcher.
  The gate drops every option Git reads as `--no-verify` or its negation, keeps every other
  argument byte for byte and in order, and adds `--verify` ahead of them and its last
  `-c core.hooksPath=jj-push-hooks/`, so `--no-verify` in any spelling, a caller's hooks path, the
  public-check wrapper and the global dispatcher cannot skip the term scan. For a verified plain
  push, `GIT_SHIM_OWN_HOOKS` names the repository
  hook directory; after a clean term check and public check where it is enabled, the gate runs
  that own pre-push with the same arguments and update input, without the shim's `-c` or that
  variable. Where the wrapper or dispatcher is effective, the public check runs once through it.
  `GIT_SHIM_JJ_PUSH=1` tells the gate that jj ran the push, so its refusals say `jj push`, else
  `push`.
  The existing public-gate decisions remain: only the existing agent jj and verified plain-push
  paths set `GIT_SHIM_PUBLIC_CHECK`; the term gate is separate, so it also runs for a person's
  push and wherever the existing public gate would not. A Git executable run by its full path,
  or a remote changed between the shim's decision and Git reading its configuration, remains
  outside both shim checks.
  Unknown counts as public: a cache that cannot be reached or read, an entry that is empty or
  holds another word, gh that fails or gives no answer within 5 s, guards the checkout, and the
  check then refuses a push to a theorem-labs repository whose visibility it cannot read either.
  Each refusal line starts with `error: `, and the shim leaves out the one "failed to push some
  refs" line git writes right after the gate's refusal, so jj shows the commit and the diagnostic
  even under `--quiet`; another destination's failure keeps its line. `tests/test_jj_push_leak_gate.py`
  is the contract; when the sweep is beside the check it runs the sweep's own code on the same
  checkouts, and fails when its decision and the shim's differ. Its dispatcher cases run the tag's
  `install-global-hooks.sh` from `LEAK_CHECK_HOOKS_SOURCE`, else from
  `~/.local/share/public-leak-check-hooks`, and are skipped without one.
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
  else `mise which` it (the directory's pin, then the machine's, `-C /`), asked without HOME ("A
  moved HOME" below); arguments, stdin and PATH go through unchanged. They are for a claude or codex
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
  silently loses its cap is the failure it exists to prevent. A cgroup a detached process keeps
  is removed by a later run once it is empty and the run that made it has ended (that run's pid,
  in the cgroup's name, no longer runs), so a concurrent run's fresh, still-empty cgroup is never
  swept (dispatch://INFRA-4383). The move into the cgroup is made
  with `sudo` (cgroup v2 asks the mover for write access to the common ancestor's `cgroup.procs`,
  and the box's root cgroup is root-owned); the command itself still runs as the session user. On
  the host, and for any call that is not `--user --scope`, it execs the first real `systemd-run`
  past its own PATH entry. `shims/tests/test_systemd_run_box.py` drives the whole shim against a
  cgroup tree in a temp dir (`AGENTBOX_CGROUP_ROOT`), and its `RealCgroup` cases run it where sudo
  without a password reaches `/sys/fs/cgroup`, each in a cgroup of its own there, so they read
  only what they made and run in parallel.
- **`secrets`** refuses every agent request for a human-tier key
  (exit 77), and names the broker command (`secret-run`) or, in a session the broker does not
  serve, `secret-run --register`, which registers that one command, and the `omp --resume` line
  that relaunches the session as one. secretsd's only prompt for such a key
  is a YubiKey blink the firmware holds about 15 seconds, and on 2026-11-01 thirteen agent
  requests timed out on it. An agent is a caller with `OMP_SESSION_ID`, `PI_SESSION_FILE`,
  `CLAUDECODE` or `ENVOY_SESSION_ID` set; every other call, and every call from Sami's shells,
  execs the first `secrets` past its own PATH entry with the same arguments, else the one mise
  names, asked without HOME ("A moved HOME" below). A shell that ran
  `secrets` before the shim existed has the old path hashed and bypasses it until `hash -r`.
- **`bq`** puts BigQuery's CLI on PATH at all. The Cloud SDK installs it beside gcloud in the
  SDK's own bin dir, which is on nobody's PATH here, so every caller named the SDK's full path
  instead (agent-infra's spend loaders, 2026-10-29). It takes the first `bq` past its own PATH
  entry (`shims/ssh-keygen`'s lookup), else the SDK's install location under the home gcloud uses,
  then under the uid's passwd home ("A moved HOME" below), and
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
  of its own. It takes the first `pulumi` past its own PATH entry (`shims/ssh-keygen`'s lookup),
  else the one mise names, asked without HOME as `shims/az` asks it ("A moved HOME" below).
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
  (`shims/ssh-keygen`'s lookup), else the mise pin (`"pipx:azure-cli"` in `mise.toml`), asked
  without HOME, also in place of mise's own shim under a HOME that holds no mise configuration
  ("A moved HOME" below), and is
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
  and an Azure box pays no metadata lookup for it: off a Google VM nothing opens the store.
  On one, `scripts/gcloud-drop-azure-credential` is the store's one reader: it decides and
  removes in the same open and prints nothing when there is nothing to remove. A check that
  removed nothing is kept in a marker under `$XDG_RUNTIME_DIR` (else `~/.cache`) per config dir,
  with the store's mtime, size and inode, which `stat` reads without opening it, and the boot's
  id; that store, or any store in that dir later in the same boot, is not opened again, since on
  a Google VM nothing writes an Azure entry (only `installers/gcloud-box.sh` does, on an Azure
  box). The cost: a dir copied from an Azure box over a path already checked this boot is noticed
  at the next boot; a dir at a new path has no marker and is checked. A grep read the store
  before every gcloud call until 6c93d4e0 (2026-11-06), and after every write to the store until
  73bc4345 (2026-11-07); the Datadog CWS rule `theorem_gcloud_credential_read` reported it as a
  credential-file read 109 times between 2026-10-30 and 2026-11-06, all of them this shim
  (theorem-labs/agent-infra#2328 proposed excepting the rule instead; an `exec -a ""` caller can
  hide an extra grep argument from argv parsing, so the exception was dropped and the reads
  were), and a [high] one at 09:15Z on 2026-11-06 cost a triage
  (`shims/tests/test_gcloud_shim_routing.py`, `GcloudAzureCredentialOnGoogleVm`).
  In an agent session the shim refuses, on every route, each call that writes or removes the
  well-known file: `auth application-default login|revoke`, and an `auth login` with
  `--update-adc` or a `--flags-file`. Only a routed bare login dropped `--update-adc` before,
  so an agent's login under the user's own configuration with `--update-adc`, started at
  2026-11-01T02:41Z, wrote the user's login there at 02:53Z. It also refuses a call whose
  account is a person's own login (anything but `*.gserviceaccount.com`), whichever way the call
  reaches it: named, as the `ACCOUNT` argument of `auth print-access-token`, `print-identity-token`,
  `print-refresh-token` or `describe` (which loads that account's stored credential ahead of
  `--account` and the configuration), as `--account` or as `CLOUDSDK_CORE_ACCOUNT`; or, where the
  call names no account, as the account of the configuration it runs under, when anything but an
  explicit `--configuration` in argv chose that configuration: `GCLOUD_ACCOUNT`,
  `CLOUDSDK_ACTIVE_CONFIG_NAME`, `active_config` or gcloud's `default`. The one way past it is an
  explicit `--configuration` naming the configuration that holds that login: the route an apply
  that no declared identity can make yet takes under Sami's standing permission, each use recorded
  (dispatch://INFRA-6/ask/6c7b1e80-176f-40e3-967e-e5d642d501ec), which the refusal names wherever a
  configuration holds the login. A command whose change outlives the call
  (`persists_config_change`: `auth login`, `auth revoke`, `config set`, …) keeps the configuration
  it names, and over the SSH rule a call that chose nothing gets `box` instead (below). Until
  2026-11-17 only `--account` and `CLOUDSDK_CORE_ACCOUNT` were read, so in an agent session
  `CLOUDSDK_ACTIVE_CONFIG_NAME=theorem gcloud auth print-access-token`, and the same with the
  login as its `ACCOUNT` argument, ran as the person while `--account <the login>` was refused.
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
  an hour. An agentbox session, which has no such scratch, gets its copy at
  `$XDG_RUNTIME_DIR/gcloud-session/<the session its marker names>/gcloud`, each directory the
  user's own with mode 0700: until 2026-11-17 it was `${TMPDIR:-/tmp}/agentbox-gcloud-session`,
  one fixed name in the host's shared `/tmp` for a host process with a box's marker. Left alone: a
  `CLOUDSDK_CONFIG` the caller set, a session with neither a scratch nor a private runtime
  directory, and a copy that cannot be made. A change that must outlive the session (`auth login|revoke|activate-service-account|
  application-default|configure-docker`, `config set|unset|configurations`, `init`, or a path an
  unknown flag makes ambiguous) runs on `~/.config/gcloud`, and the session's copy is set aside so
  its next call copies again. A sign-in made outside a running session reaches its copy only after
  the session runs one of those commands, or in its next session. A copy serves only the
  configuration directory it was made from, which its `.gcloud-session-copied` names: a call whose
  `HOME` gives another directory (a child with a `HOME` of its own, a test with a fake one, in the
  same scratch) runs on its own directory. Until 2026-11-17 any copy was taken, and gcloud tests
  running in parallel under the agentbox marker, with no `TMPDIR`, judged a person's login by a copy
  another test's `HOME` had published in `/tmp`, and ran the call.
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
- **mise off PATH:** `az`, `claude`/`codex`, `ctx`, `knives`, `omp`, `pulumi`, `pyright`,
  `basedpyright` and `secrets` call mise. A unit's, a test harness's or a git credential helper's
  PATH can hold this dir without `~/.dotfiles/bin`, where mise lives; these shims then run the
  `bin/mise` of the dotfiles checkout they are in by its path, with the data dir and configuration
  of "A moved HOME" below, and in a checkout with no `bin/mise` the mise that mise's shims link to.
- **`gcloud`**'s global-flag table is the installed SDK's. After any `gcloud components update`,
  run `python3 scripts/gcloud-global-flags --check`; on a difference, regenerate the table with
  `python3 scripts/gcloud-global-flags`, and the test fixture with `--json`. Run it with `python3`,
  not `bash`.
- `gws`: an executable shim that resolves the gws-accounts wrapper installed by the omp plugin pin
  (`omp/plugins/package.json`). Its marker lets the package wrapper skip the shim while finding real
  gws; if the package is absent, the shim fails loudly and directs `bun install` in `omp/plugins`.

## Leak-term list

`leak-terms.sops.yaml` at the repository root is encrypted to the default creation rule of
`.sops.yaml`. Edit it only with `sops edit leak-terms.sops.yaml`. `leak-terms-check` decrypts its
`terms` value with sops through a pipe; terms remain only in the scanner's memory, never in a
file, command argument or environment variable. `DOTFILES_LEAK_TERMS_FILE` selects a different
encrypted list for a run.

The list has three parts. `terms`, a block of lines, is for every repository. `scoped`, a map of
repository patterns (`owner/name`, shell wildcards, letter case ignored) to blocks of lines,
holds terms that are private in some repositories and may stand in others: `cybermonklabs/*`
holds cybermonklabs/legion's private names, which reached its public main in pushes the list did
not hold (dispatch://INFRA-4102), while another public repository may name some of them. A commit
reads `terms` alone, since it goes nowhere yet. A push reads `terms` and each scoped block whose
pattern matches its destination (`github-repo-visibility --slug`; a destination it cannot name
matches every pattern). gh text reads both, and a third source: the `forbidden` list of each
entry of knives's fork registry (`~/.config/knives/repos.toml`, and the private
`repos.local.toml` beside it, whose values knives adds to the entries it names; under
`XDG_CONFIG_HOME` or `HOME`, else the passwd home's; `DOTFILES_LEAK_TERMS_KNIVES_DIR` for a test;
a machine with none has no such list, and one that cannot be read refuses). An entry's list is
for every repository named as its `upstream`, `origin` or `release`, whatever the owner: a fork
keeps its upstream's name, and oh-my-pi's fork moved from sjawhar to cybermonklabs while the
registry still names sjawhar. A push reads no registry list: the fork's own release branch names
several of those terms, and the lists are for what is posted to the repositories they name. A
term two lists hold counts in each, since each says where its terms may not go.

`allowed`, a third map of repository patterns to blocks of exact tokens (with `#` lines giving the
reason), lets a token that holds a term stand in those repositories: a match that lies wholly
inside such a token, written exactly so (letter case included) and whole (no letter, digit, `.`,
`_`, `-` or `/` after it, nor those or `@` before it), is no match there. cybermonklabs/oh-my-pi
allows one npm package, whose scope is term 20 of `cybermonklabs/*`, a public npm organisation's
name: upstream's browser-MCP filter names it (upstream d5b28d6d, PR #13896), as written and as a
JavaScript regular expression escapes its slash. The bare term, the scope with any other package
and the token in any other repository stay refused. A push applies its destination's tokens, gh
text each target's, a commit none, and a destination nobody could name none.

For tests, `DOTFILES_LEAK_TERMS_SOPS_HOME` makes sops use that directory as both `HOME` and
`XDG_CONFIG_HOME`. Otherwise, when no `SOPS_AGE_KEY`, `SOPS_AGE_KEY_FILE` or `SOPS_AGE_KEY_CMD`
is set and the configured age key does not exist, the scanner gives sops the passwd-home
`~/.config/sops/age/keys.txt` when it exists. A match ignores case and treats each run of spaces in a
term as one or more spaces or tabs; blank list lines and lines that start with `#` are no terms.
Prose is read as GitHub renders it, a paragraph at a time: a commit, tag or jj message, gh text,
and the added lines of a file named `*.md`, `*.mdx`, `*.txt` or `*.rst` in a commit, a push or a
merge. A line break inside a paragraph, a CRLF or a no-break space counts as a space there, and a
blank line ends the paragraph. Every other file, and a file object pushed with no path, is read a
line at a time: code is not reflowed, and joining its lines would refuse words no reader sees side
by side. Until 2026-11-17 every line was read alone, and the list holds terms of several words.

The scanner runs the first `sops` on `PATH`, except mise's shim: in its place it runs the version
this checkout's `mise.toml` pins, from `installs/sops/<version>/` in the mise data directory that
holds that shims directory. mise's shim takes its version from the caller's `HOME` or from a
`mise.toml` in the caller's directory, so with a test's `HOME` outside this checkout it had none,
exited 1, and every commit a Legion test made through the shims was refused (2026-11-16). With
no exact pin or no install of it, the shim runs as found. With no sops on `PATH` at all (a Legion
test pane's `<bin>:/usr/local/bin:/usr/bin:/bin`), it runs that pinned version from mise's data
dirs as "A moved HOME" below reads them, and its refusal names each dir it searched. A sops that exits with any status but
0 or 128 is named in the refusal with its status.

Every mode prints one stderr line per match that names the place (a file and its line, a message
and its line, a commit, a tag, a ref, or a gh call's field, the value's path in it and the line in
that value), the text that matched as it is written, the list that holds the term, and the line it
is on, then says how to remove it (dispatch://INFRA-4383):
`leak terms: notes.md:3: "Quillfeather Lane 9" (the global list), in: see Quillfeather Lane 9 there`.
The list is `the global list` (`terms`), `the <pattern> list` (a `scoped` block) or `the knives
<entry> list` (the registry's), so the advice to drop a term that is not private names the right
file. A place that is the matched name itself (a path, a ref, a tag's name) shows no line. The
text and its line are C-escaped, so each refusal is one line (a term a wrapped line splits shows as
`Quillfeather\nLane 9`). The line is cut to 80 characters on each side of the match, and the match
to its first and last 80 characters (a term whose words a long run of spaces parts), each cut
marked `…`. Until 2026-11-17 a refusal named the place only, with `listed leak term` (in gh mode,
the term's number in a list the caller cannot decrypt), so the agent that wrote the text could not
tell what to change. The caller wrote that text, so showing it copies nothing to a new place.

The `diff`, `staged`, `message` and `push` modes exit 0 when clean, 3 on a match, 2 when the list
cannot be read and 4 when the text to scan cannot be read; `jj-editor` returns a nonzero status to
stop jj after a match or other failure. Every shim treats any nonzero scan result as a refusal. A
missing list or sops, an undecryptable or invalid list, an empty list, a malformed diff or an
unreadable push advertisement therefore fails closed.

The `gh` mode (`leak-terms-check gh -- <gh argv>`, the text a gh call sends; `gh` above) prints
`leak terms: gh <command> to <target>: <field> line <n>: "<text>" (<list>), in: <line>` for each
match. It exits 3 on a match, 2 when the list cannot be read and 4 when a file the call names
cannot be read, each only for a call that can reach something not confirmed private, and 7 to ask
for the call's stdin; `shims/gh` exits 2 on any of those but 7.

Every place a refusal reaches is the caller's own, so each shows the text (dispatch://INFRA-4383,
R3):

- The stderr of the command that ran the check, which is a person's terminal or an agent's own
  session: `shims/gh` (the check writes to the shim's stderr), the `git-hooks/hook` pre-commit and
  commit-msg checks that `shims/git` installs, `jj-push-hooks/pre-push` (each line prefixed
  `error: `), and `shims/jj`'s commit, describe and new checks and its push gate (each push line
  prefixed `error: `).
- For a push jj runs, `shims/git` (`jj_push_gate`) passes git's stderr, the pre-push hook's
  refusal in it, through an `awk` filter that drops git's own `failed to push some refs` line
  after the gate's refusal, and writes the rest to its own stderr, which jj shows, under `--quiet`
  too; that is the jj caller's terminal or session.
- `shims/gh`'s outermost pass in an agent session copies the call's stderr into a private
  temporary directory (`mktemp -d` under `TMPDIR`, removed when the call ends) only to find GitHub's
  refusal of the call or a spent rate limit (`gh` above); it prints none of that copy, and reads it
  only when the call reached gh.
- No log: the gh call log under the read cache's root records each call's argv, not its stderr,
  and the read cache stores a call's stderr only for a call that succeeded (exit 0, or 8 with no
  stderr), never for a refusal.
- No CI: neither workflow of this public repository (`.github/workflows/envoy-typecheck.yml`,
  `.github/workflows/omp-extensions-test.yml`) runs these tests or the check, and Legion does not
  run it (its pods run the shared public leak check, whose refusals are that check's own).

A refusal that would reach a public place must show the kind of each match (`<term>`) in place of
its text; none does. A refusal pasted into a gh post bound for a repository not confirmed private
is refused by the gh mode itself.

A machine whose age key is not a recipient refuses commits, guarded pushes and gh text bound for a
repository not confirmed private. Add that key above the YubiKey line of `.sops.yaml`, then run
`sops updatekeys leak-terms.sops.yaml` on a machine that can decrypt the list.

Tests that commit, push or write through gh with the shims get their isolated list and knives
registry from `shims/tests/leak_terms_fixture.py`. The `shims/tests` and `scripts/tests` conftests
put its four variables in `os.environ` for pytest; a fixture that builds an environment from
scratch must retain `DOTFILES_LEAK_TERMS_FILE`, `SOPS_AGE_KEY_FILE`,
`DOTFILES_LEAK_TERMS_SOPS_HOME` and `DOTFILES_LEAK_TERMS_KNIVES_DIR`, and a `PATH` that finds sops:
`leak_check_environment(path)` gives all five, with sops last on that `PATH`.

## Who is an agent

`az`, `gcloud`, `gh`, `git`, `jj` and `tmux` each hold a copy of one `agent_context` function,
`secrets` makes the same test inline and `gh-app-token` makes it in Python. All eight read one
marker set: `OMP_SESSION_ID`, `PI_SESSION_FILE`, `PI_TOOL_BRIDGE_URL`, `AGENTBOX_SESSION`,
`CLAUDECODE`, `ENVOY_SESSION_ID`, `AGENT_SHIM_AGENT`, Legion's `LEGION_ROLE`, `LEGION_TREE`,
`LEGION_CONTROLLER` and `LEGION_DAEMON_URL` (`packages/daemon/internal/runtime/tmux/spawn.go`),
and the gh-app routing include. Until round 3 of dispatch://INFRA-1538 `secrets` read four of
those and no routing include, and no shim knew Legion at all. A caller is also an agent **when
`SSH_CONNECTION` is set, `SSH_TTY` is not, and its session has no controlling terminal**: `ssh
sami-devbox az account show` carries no marker, and until 2026-11-07 every such command ran as the
person whose login the machine holds — az with `~/.azure`, Sami's own.

The SESSION decides, by its terminal: not the command's own stdin, and not `SSH_TTY` alone. A
person's session has a controlling terminal — sshd's for an interactive login, tmux's for every
pane — and every process of it shares it, so `echo x | gh …` in a person's shell, and git handing
a credential helper its request down a pipe, stay the person's; the first draft tested
`[[ ! -t 0 ]]` and made both an agent's. A command run as `ssh host cmd` without `-t` has none:
opening `/dev/tty` fails there with ENXIO, and opens under `ssh -tt` and in a tmux pane (measured
against a real sshd on 2026-11-17). Until then the rule read `SSH_TTY` alone, and a tmux server
started before `.tmux.conf` carried `SSH_TTY` gives its panes `SSH_CONNECTION` without it: in
Sami's own pane, `gh auth status` said "non-interactive SSH, so an agent's call"
(dispatch://INFRA-4089). The bash copies open the terminal with `{ true < /dev/tty; }`, not `:`,
because a failed redirection of a special builtin ends a POSIX-mode bash (`POSIXLY_CORRECT` in the
environment), shim and all. A person's own shell off SSH is unchanged, and `AGENT_SHIM_PERSON=1` is
their escape for a deliberate non-interactive SSH command of their own; it does not turn a session
marker off, and a marker means an agent whatever the terminal.

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
  by default, so the variable stays true in the windows a person opens after reattaching over
  ssh. The rule does not depend on it: what runs in a pane has the pane's terminal, so the SSH
  rule does not decide there (short of a process that leaves the session, `setsid`), and a pane
  is an agent's through the marker this shim or the relay gave it.

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
- A command that has a terminal is the person's to the SSH rule, so an agent whose only evidence
  is that rule and that gets itself a terminal — `script -c …`, a tmux server it starts past this
  shim — is past the rule. `ssh -t` always was (sshd sets `SSH_TTY` for it). Every agent harness
  sets a marker, which decides whatever the terminal; the rule is for the bare `ssh host cmd`.

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
  reached that way, other than through an explicit `--configuration`, is refused earlier, by the
  agent-session account guard); and where this machine
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
end-to-end case. Each case names its session (`shims/tests/stdin_cases.py`): `run_without_tty`
starts it with no controlling terminal, as `ssh host cmd` is started, and `run_with_tty` with a pty
as its controlling terminal, as a pane or `ssh -t` is, with that pty or a pipe on stdin, so the
terminal of whoever runs the suite decides nothing. A test that means a person must drop
`SSH_CONNECTION` from the environment it inherits, set `SSH_TTY` beside it, or run with a
terminal; a test fixture that commits must bring its own `HOME`, git configuration and author, or
whoever runs the suite decides the case.

## Adding to a shim

A shim composes with everything else that wraps its binary or already does what it adds.
Before adding a side effect or a binary lookup, search for the callers that already do the
same thing and for every other wrapper of the same binary, not only for the callers you mean
to protect: a lock the shim adds can collide with a caller's own lock, and a lookup can find a
pass-through wrapper that hands the call straight back to the shim. A shim that resolves its
real binary takes the first match past its own PATH entry, never the first from the top, and
a pass-through wrapper hands the next binary the PATH tail past its own entry, so nothing
below it can find the wrapper again. `shims/gh` searched from the top until 2026-11-18, when a
test of cybermonklabs/legion put its pane's `gh` wrapper above the shim and gave `legion gh` the
shim, by its path, as its real gh: the shim took the wrapper for the real gh, and the chain of
wrapper, `legion gh` and shim grew about 2,000 processes deep (4,275 processes) until the user's
memory scope reached its ceiling and two workers died. It now takes the first gh past its own
entry, and the first from the top only when it runs by a path outside PATH
(`shims/tests/test_gh_wrapper_loop.py`). `shims/gcloud` still searches from the top, which is
safe only while nothing puts a gcloud wrapper above it, and its routing tests depend on it: they
put a stub `gcloud` above this dir and expect the shim to hand the call to the stub. It skips a
copy of itself (same bytes, or its marker line), since two copies on PATH called each other
until an agent box's 8192 processes were used up on 2026-11-03 (`GcloudShimCopiesOnPath`).
`shims/jj` and `shims/tmux` search from the top too, and hand the binary they find the whole
PATH: jj's own `git` calls must still reach `shims/git`, and a tmux server started through the
shim gives its PATH to every pane. The first
jj or tmux they find is usually mise's shim. In its place they run the install of the exact
version mise's global configuration pins, found without HOME, and past PATH they run that install
too ("A moved HOME" below). Where it is missing they run mise's shim, which hands the call back to
the first one on PATH whenever mise has no version of it for the directory (a relocated `HOME` or
`XDG_CONFIG_HOME` hides mise's global config). So `DOTFILES_JJ_SHIM` and `DOTFILES_TMUX_SHIM`
name every binary the shim has handed the call to in that pid, and a re-entry in the same pid
skips each one (and every path to the same file), taking the next one on PATH, then the pinned
install, or failing with exit 127 and one line naming PATH, the configuration and the install
dirs it read, instead of looping. `DOTFILES_GH_SHIM` does the same for gh, and also counts a
re-entry in another pid below the hand-off that carries the same call (`gh_shim_call_digest`,
the digest of its citation and argv): a wrapper that runs the shim as its real gh may start it
as a child, as `legion gh` does, and a shim run by a path outside PATH still takes the first gh
from the top, so a pid alone never sees that loop. Any other call below the real gh, such as the
git credential helper gh runs, skips nothing. Where no digest can be made (no `sha256sum` on
PATH: the marker then holds `-`) or a wrapper changes the argv on each pass, the same call cannot
be told, and the marker's hop count holds the guard: bash counts every hand-off to a gh in the
chain of processes, with no outside tool, and the shim refuses the hand-off after the eighth
(`_max_hops`) with exit 127 and one line naming PATH. The deepest chain that is no loop has about
four (knives' hand-off, its own gh query, the git credential helper gh runs, a hook's gh). Until
the hop count, a wrapper above a shim run by its path, with only that wrapper and a real gh on
PATH, looped (97 wrapper passes and no call to the real gh before a capped review run killed it at
101 processes).

A shim that finds its real binary by a marker in its first 512 bytes must keep that marker in its
header: `git-worktree-shim` for `shims/git` and `jj-lock-shim` for `shims/jj`. On 2026-11-12 a
draft of the leak-term check (dispatch://HOME-131) rewrote the header of `shims/jj` without
`jj-lock-shim`; the shim found itself as the real jj, and its editor wrapper started copies of
itself until the machine ran out of memory. A test that runs a shim, jj or an editor runs it in a
cgroup of its own whose task limit counts only that command's processes and threads
(`systemd-run --user --scope -p TasksMax=…`; in an agent box, the cgroup `shims/systemd-run`
makes), and under a timeout that kills that cgroup and the command's process group. A command
that forks without end stops at the limit and dies at the timeout, and every other process of the
user forks as before (`shims/tests/test_leak_terms.py`, `test_harness_*`). The cap was the
user-wide `ulimit -u` at the user's live tasks plus 1000 until 2026-11-16; other sessions on a
288-CPU box grew by more than that within seconds, and the tests' own jj and git forks failed with
`Resource temporarily unavailable` in about two of three runs.

## A moved HOME

A caller may give a shim a HOME of its own. A Legion test pane starts Oh My Pi with exactly
`HOME=<temporary dir>` and `PATH=<bin>:/usr/local/bin:/usr/bin:/bin`, where `<bin>/jj` is a link
to the jj the test process found, which on a dotfiles machine is `shims/jj`. Every path under such
a HOME names nothing: no `~/.dotfiles`, no `~/.mise`, no mise configuration and no record of the
configurations mise trusts. Until 2026-11-17 `shims/jj` looked past PATH only at
`$HOME/.dotfiles/bin/jj` and `$HOME/.mise/shims/jj`, so every jj in such a pane exited 127 with `jj
shim: no real jj found on PATH`, and ten pi-legion rows that run the real binary failed on
sami-devbox while CI passed them. The same pane showed two more faults of the class: `shims/jj` and
`shims/git` found `scripts/` and their hook dirs beside the path they were run by, the pane's link,
not beside themselves, so every jj commit there was refused (`cannot read the diff`) and a git
commit through such a link ran with no leak-term scan at all; and `scripts/leak-terms-check` found
sops only on PATH, which holds none there.

So no shim finds its real binary, or a file of its own checkout, through HOME or through the path
it was run by. `jj` and `git` take their own directory through a link to them (`_shim_dir`);
`gh`, `claude`/`codex`, `omp` and `scripts/agent-session` take the checkout from `DOTFILES_DIR`,
else from their own resolved path. Each shim that finds a binary through mise holds a copy of
`mise_paths`, which takes:

- the dotfiles checkout from `DOTFILES_DIR`, else from the shim's own path (`readlink -f`), so a
  link to the shim in another directory finds it too;
- mise's global configuration from `MISE_GLOBAL_CONFIG_FILE`, else from under HOME when that file
  is readable, else the checkout's `mise.toml`, which installers link mise's global config to;
- mise's data dirs from `MISE_DATA_DIR` and mise's default `~/.local/share/mise` under HOME (where
  a `mise install` run without `.bashrc`'s `MISE_DATA_DIR` puts a tool, as `shims/knives` and
  `shims/gh` already read); with no `MISE_DATA_DIR`, `~/.mise` (.bashrc's) and that default under
  HOME, then the same two under the uid's passwd home, which HOME does not move;
- mise itself from PATH, else the checkout's `bin/mise`, else the mise that the shims in one of
  those data dirs link to: `bin/` is not committed, so a copy or a worktree of the checkout has no
  `bin/mise`. Until 2026-11-17 the shims ran the copy's missing `bin/mise`, and eight cases of
  `shims/tests/test_shim_moved_home.py` exited 127 in a clean clone, which is where the root
  `AGENTS.md` says to test a shim change.

`is_mise_shim` and `mise_pinned` build on it. `shims/jj`, `tmux` and `gh` run the install of the
exact pin in place of mise's own shim, which under a moved HOME refuses (`Config files in
~/.config/mise/config.toml are not trusted`), and past PATH; so a project's own mise pin of jj or
tmux is not read, as gh's was not before (no repository under `~/src` pins either on 2026-11-17),
and the call skips one mise process (5 `jj --version` in 0.11 s against 0.30 s through mise's
shim, measured that day). `az`, `secrets` and `pulumi` ask that
mise past PATH, and in place of mise's own shim when HOME holds no mise configuration. `knives`,
`claude`/`codex`, `ctx`, `pyright`, `basedpyright` and `omp` give mise that configuration and data
dir. `bq` and `gcloud` look for the Cloud SDK under the passwd home after HOME. `gh-app-token`
gives the `secrets` and `agent-secrets` it runs through mise's shims the same data dir and
configuration, and `scripts/leak-terms-check` runs the pinned sops from those data dirs when PATH
holds no sops. The shell copies are kept identical by hand, as `agent_context` is, and
`shims/tests/test_shim_moved_home.py` compares them and runs each of these shims under such a
pane's HOME and PATH against the real binary, a jj commit and a git commit through a link
included.

Two binaries stay on PATH alone. Claude Code's own binary is its native install
(`~/.local/bin/claude`), not a mise tool, so `shims/claude` finds it only on PATH; and codex is an
npm package whose launcher needs `node` on PATH, which `shims/codex` passes on unchanged. State a
caller's HOME owns stays under it: caches, the gh agent config, the omp profile, tmux's sockets.

## How `shims/omp` resolves omp

`shims/omp` takes the dotfiles checkout from `DOTFILES_DIR`, else from its own resolved path, and
runs `mise env -s bash` first, with the data dir and configuration of "A moved HOME" above.
A session keeps the PATH it started with, and that
PATH can name the install dir of an older pin; mise swaps it for the current pin's dir, so a
stale PATH still starts the pinned omp. Then `scripts/agent-session` takes the first `omp` on
PATH that is not a wrapper: it skips mise's shims dir (`~/.mise/shims`), whose `omp` hands the
call back to PATH when mise has no version for the directory, and every `omp` whose real path is
in a `shims/` directory (this shim, another checkout's copy). A local build on PATH ahead of
mise's shims dir wins: `mise env` keeps the entries ahead of that dir ahead of the tool dirs it
adds, and puts its tool dirs first on a PATH without it; with the pin not installed and no local build, the launch fails with
exit 127 instead of looping back into the shim.
Once it has that `omp`, `scripts/agent-session` adds `omp/config.probe.yml` (the subagent progress probe, on) to `PI_CONFIG_FILES`, right after `omp/config.yml` and before the machine's `config.local.yml`, only when the binary's real path is a release directory stamped `20261117-053702` or later (`…-sami.YYYYMMDD-HHMMSS/bin/omp`, dispatch://INFRA-4027), and drops it from an inherited list otherwise, so a local build, an unreadable version or a roll-back of the pin starts with the probe off.
