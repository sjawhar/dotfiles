---
name: session-attribution
description: "Use when tracing an artifact back to the agent session that produced it, or listing everything a session touched. Triggers: who made this commit, which session or agent wrote this, attribute a commit/PR/comment. Also: what did session X do, resume the session that did this, Omp-Session trailer, legion footer, session forensics."
---

# Session Attribution

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Where the id is

- Every omp-session commit gets an `Omp-Session: <id>` trailer. In git, `~/.dotfiles/shims/git` adds it. In jj, a JJ_CONFIG overlay adds it: `~/.dotfiles/omp/extensions/session-env.ts` for interactive sessions, the Legion extension for a session the Legion daemon starts.
- No trailer means "not attributable from the commit", not an error. A human's commit has none, and neither has one made through an alias, `git rebase`, `git am` or `git commit-tree`, one in a checkout that sets `git config omp.sessionTrailer false`, or any `git merge`, `pull`, `cherry-pick` or `revert` commit from before 2026-10-24. Attribute those from the author email and commit time, plus a session editing the same files at that time (`tq search --commands`, `skill://using-transcript-store`).
- Legion GitHub comments and reviews end with `<!-- legion: {"session":"<id>", ...} -->`.
- Use the `jj` lines below only where `jj root` succeeds: Legion workspaces, `~/.dotfiles`, parts of `~/src`.

```bash
git log -1 --format=%b <sha> | grep Omp-Session
jj log -r <rev> --no-graph -T description | grep Omp-Session
gh api repos/<owner>/<repo>/issues/comments/<id> -q .body | grep -o '<!-- legion: .* -->'
```

## Reach the session, live first: it can answer

1. Live: list live sessions with `envoy_sessions`, then message the one you want with `envoy_send`.
2. Resume: `omp --resume <id>`, the full id or a prefix, on the machine that holds the transcript.
3. Read: find the transcript with `ls ~/.omp/agent/sessions/*/*_<id>*.jsonl`. The directory with the same name holds:
   - `<AgentName>.jsonl` for each subagent. Its brief is the `task` field of the `session_init` record near the top.
   - `local/<name>` for each `local://<name>` file the session wrote, on the host disk. Files in `/tmp` or an agent container are gone with it.

## List what a session touched

Run both `gh search` lines, because `issues` skips PR comments. The `gh api` line is the deterministic sweep of one repo.

```bash
git log --all --format='%h %s %(trailers:key=Omp-Session,valueonly)' | grep <id>
jj log --no-graph -r 'description(glob:"*Omp-Session: <id>*")' -T 'commit_id.short() ++ " " ++ description.first_line() ++ "\n"'
gh search issues "<id> in:comments" --owner <owner>
gh search prs    "<id> in:comments" --owner <owner>
gh api "repos/<owner>/<repo>/issues/comments" --paginate -q '.[] | select(.body | contains("<id>")) | .html_url'
```

## Read the evidence

- Legion phase workers commit under the root architect's id, so the trailer names the architect, not the worker. Find the worker in comment footers and `.legion/` handoffs.
- In a shared jj checkout (`~/.dotfiles`, `~/src`), the trailer names the session that ran the next `jj` command, not the author. Treat it as the author only in a per-session workspace (`jj workspace add`). Otherwise compare the commit's files (`git show --stat <sha>`) with what each candidate edited, message them, and split by file when owners claim files (`jj split -r <rev> <paths>`).
- Slash-command or skill text in a transcript does not prove the session invoked it. An invocation is a `message` record whose role is `user` or `fileMention`; the same text in a `toolResult` or `compaction` record is a file the session read.
