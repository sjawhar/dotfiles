---
name: session-attribution
description: "Use when tracing a commit, PR or comment to the agent session that made it, listing what a session touched, or reaching that session: who made this commit, which session wrote this, the Omp-Session trailer, a Legion footer."
---

# Session attribution

## Where the id is

- An omp session's commits carry an `Omp-Session: <id>` trailer. `shims/git` adds it to git commits, the session's `JJ_CONFIG` overlay to jj commits, and `PI_COMMIT_TRAILERS` to commits omp writes itself. The header of `scripts/omp-session-trailer` says where it is left off.
- Legion role commits carry `Omp-Session` and `Legion-Role: <role>`, each from the role's own session. Legion comments and reviews end with `<!-- legion: {"session":"<id>", ...} -->`.
- No trailer means "not attributable from the commit", not an error. Commits by a person, older commits, commits made by `git rebase`, `git am`, `git commit-tree` or a shell alias, and commits in a checkout that sets `git config omp.sessionTrailer false` have none. Attribute those by author email and commit time, plus a session that edited the same files then (`tq search --commands`, `using-transcript-store`).
- Use the `jj` lines only where `jj root` succeeds.

```bash
git log -1 --format=%B <sha> | grep Omp-Session
jj log -r <rev> --no-graph -T description | grep Omp-Session
gh api repos/<owner>/<repo>/issues/comments/<id> -q .body | grep -o '<!-- legion: .* -->'   # pulls/comments/<id> for an inline review comment
```

## Reach the session, live first: it can answer

1. Live: list sessions with `envoy_sessions`, then message one with `envoy_send`. For a commit, `scripts/tell-head-session <owner/repo> <sha> <message>` messages the session its trailer names.
2. Resume: `omp --resume <id>` (an id prefix works) on the machine that holds the transcript.
3. Read: `ls ~/.omp/agent/sessions/*/*_<id>*.jsonl`. The directory of the same name holds:
   - `<AgentName>.jsonl` for each subagent. Its brief is the `task` field of the `session_init` record near the top.
   - `local/<name>` for each `local://<name>` file the session wrote. Files it left in an agent box's home or `/tmp` died with the box.

## List what a session touched

Run both `gh search` lines: `issues` skips PR comments. For one repository, the `gh api` lines sweep conversation comments on issues and PRs (`issues/comments`) and inline review comments (`pulls/comments`). Review bodies have no repository-wide endpoint: read `pulls/<n>/reviews` for each PR.

```bash
git log -n <N> --format='@@@%h %s%n%B' <ref> | awk -v id='<id>' '/^@@@/{h=substr($0,4); next} $0 ~ ("^Omp-Session: " id) {print h}' | sort -u
jj log --no-graph -r 'description(glob:"*Omp-Session: <id>*")' -T 'commit_id.short() ++ " " ++ description.first_line() ++ "\n"'
gh search issues "<id> in:comments" --owner <owner>
gh search prs    "<id> in:comments" --owner <owner>
gh api "repos/<owner>/<repo>/issues/comments" --paginate -q '.[] | select(.body | contains("<id>")) | .html_url'
gh api "repos/<owner>/<repo>/pulls/comments" --paginate -q '.[] | select(.body | contains("<id>")) | .html_url'
```

- The `git log` line matches the whole message, not `%(trailers)`: a merge-queue squash appends a `Co-authored-by` paragraph after the `Omp-Session` lines, and git reads trailers from the last paragraph only. Widen `-n` until it reaches the session's first commit. In a jj checkout, `git log --all` also counts hidden copies (`refs/jj/keep/*`).
- A merge commit from a merge queue that uses the `MERGE` method has an `Omp-Session` line only if the PR body did. The branch's commits keep theirs: use `<merge>^1..<merge>^2` as `<ref>`.

## Read the evidence

- jj adds the trailer when a commit is described or committed. In a working copy several sessions share (`~/.dotfiles`, a canonical `~/src/<repo>`), it names the session that described the commit, not the author of each file. Treat it as the author only in a per-session workspace. Otherwise compare the commit's files (`git show --stat <sha>`) with what each candidate edited, ask them, and split by file when owners claim files (`jj split -r <rev> -m '<message>' <paths>`).
- Skill or slash-command text in a transcript does not prove the session invoked it. An invocation is a `message` record whose role is `user` or `fileMention`; the same text in a `toolResult` or `compaction` record is a file the session read.
