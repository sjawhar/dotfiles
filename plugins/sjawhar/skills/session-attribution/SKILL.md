---
name: session-attribution
description: "Use when tracing an artifact back to the agent session that produced it, or listing everything a session touched. Triggers: who made this commit, which session or agent wrote this, attribute a commit/PR/comment. Also: what did session X do, resume the session that did this, Omp-Session trailer, legion footer, session forensics."
---

# Session Attribution

## Where the id is

- Every omp-session commit gets an `Omp-Session: <id>` trailer automatically. In git, `shims/git` adds it. In jj, a JJ_CONFIG overlay adds it: `omp/extensions/session-env.ts` for interactive sessions, the Legion extension for Legion roots.
- A git commit made through an alias, a merge, a cherry-pick or `git commit-tree` has no trailer. Attribute it by author, timestamp and transcript correlation.
- A commit in a public theorem-labs repository (or one whose visibility GitHub could not report when it was made) has no trailer either, by design: `shims/git` and `shims/jj` leave it off there (`scripts/omp-session-trailer`). Attribute it the same way.
- Legion GitHub comments and reviews end with `<!-- legion: {"session":"<id>", ...} -->`.
- `omp --resume` accepts the id or a prefix of it.
- `jj` lines work only in jj repos: Legion workspaces and the shared `~/.dotfiles` and `~/src` stores.

```bash
git log -1 --format=%b <sha> | grep Omp-Session
jj log -r <rev> --no-graph -T description | grep Omp-Session
gh api repos/<owner>/<repo>/issues/comments/<id> -q .body | grep -o '<!-- legion: .* -->'
```

## Reach the session, in order

1. Live: `hub` op `list` or `envoy_sessions`. Message it.
2. Resume: `omp --resume <id-prefix>` on the machine that has the transcript.
3. Read: `~/.omp/agent/sessions/<project-slug>/<timestamp>_<id>.jsonl`. The directory with the same name holds:
   - `<AgentName>.jsonl` for each subagent. Its brief is the `task` field of the `session_init` record near the top.
   - `local/<name>` for each `local://<name>` file the session wrote, on the host disk. Files in `/tmp` or a box workspace are gone with the box.

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

- No trailer on an old commit or a human's commit means "not an agent commit", not an error.
- Legion phase workers commit under the root architect's id. Find the worker in comment footers and `.legion/` handoffs.
- In a shared jj checkout (`~/.dotfiles`, `~/src`), the trailer names the session that ran the next `jj` command, not the author. Trust it only in a per-session workspace (`jj workspace add`). Otherwise attribute from content, ask the candidate sessions, and split the commit by file when owners claim files.
- Command text in a transcript does not prove that the session ran it. An invocation is a `message` record whose role is `user` or `fileMention`. The same text in a `toolResult` or `compaction` record is a file read. Example: the post-compaction reminder puts `commands/sdd.md` in most long transcripts.
