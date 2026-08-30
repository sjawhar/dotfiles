---
name: session-attribution
description: "Use when tracing an artifact back to the agent session that produced it, or listing everything a session touched. Triggers on: who made this commit, which session/agent wrote this, attribute this commit/PR/comment, what did session X do, resume the session that did this, Omp-Session trailer, legion footer, session forensics."
---

# Session Attribution

Every jj commit made from an OMP agent session carries an `Omp-Session: <session-id>` trailer
(injected automatically via a `JJ_CONFIG` overlay — `omp/extensions/session-env.ts` for
interactive sessions, the Legion extension for Legion roots). The id is the resumable session
id: `omp --resume <id>` accepts it, prefix included. Legion GitHub comments and reviews carry
an HTML footer with the same kind of id.

## Artifact → session

**Commit** (jj or git checkout, any machine):

```bash
jj log -r <rev> --no-graph -T description | grep Omp-Session
git log -1 --format=%b <sha> | grep Omp-Session          # git-only checkout
```

**GitHub issue/PR comment or review** (Legion-posted):

```bash
gh api repos/<owner>/<repo>/issues/comments/<id> -q .body | grep -o '<!-- legion: .* -->'
```

The footer JSON is `{"session":"<id>", ...}`.

## Session → reachability

With an id in hand, in order:

1. **Live?** `hub` op `list` / `envoy_sessions` — if the session is registered live, message it.
2. **Resume it:** `omp --resume <id-prefix>` on the machine where the session ran.
3. **Read only:** transcript at `~/.omp/agent/sessions/<project-slug>/<timestamp>_<id>.jsonl`;
   subagent transcripts are `<AgentName>.jsonl` inside the session's same-named directory.

## Session → artifacts (inventory)

```bash
git log --all --format='%h %s %(trailers:key=Omp-Session,valueonly)' | grep <id>
jj log -r 'all()' --no-graph -T 'commit_id.short() ++ " " ++ description.first_line() ++ "\n"' \
  -r 'description(glob:"*Omp-Session: <id>*")'
gh search issues "<id> in:comments" --owner <owner>          # issue/PR comments (search index)
gh api "repos/<owner>/<repo>/issues/comments" --paginate \
  -q '.[] | select(.body | contains("<id>")) | .html_url'   # deterministic per-repo sweep
```

## Caveats

- Commits predating the mechanism, and commits humans make in their own terminals, have no
  trailer — absence means "not an agent session commit", not an error.
- Legion phase workers are subagents of the root architect: their commits carry the **root**
  session id. Worker-level provenance lives in the comment footers and `.legion/` handoffs.
- The trailer names the session, not the machine. Resume works where the transcript lives.
