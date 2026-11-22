---
name: session-attribution
description: "Use when tracing a commit, PR, comment or other artifact to the agent session that made it, listing what a session touched, reaching or resuming that session, or doing session forensics: who made this commit, which session or agent wrote this, the Omp-Session trailer, a Legion footer."
---

# Session attribution

## Where the id is

- An omp session's commits carry an `Omp-Session: <id>` trailer.

  | Commits | Trailer added by |
  | --- | --- |
  | git commits | `shims/git` |
  | jj commits | The session's `JJ_CONFIG` overlay |
  | Commits omp writes itself | `PI_COMMIT_TRAILERS` |

  | `JJ_CONFIG` overlay of | Written by |
  | --- | --- |
  | An interactive session | `omp/extensions/session-env.ts` |
  | A session the Legion daemon starts | The Legion extension |

- The header of `scripts/omp-session-trailer` says where the trailer is left off.
- Legion role commits carry `Omp-Session` and `Legion-Role: <role>`, each from the role's own session.
- Legion comments and reviews end with `<!-- legion: {"session":"<id>", ...} -->`.
- No trailer means "not attributable from the commit", not an error.
- These commits have none:
  - Commits by a person.
  - Commits made by `git rebase`, `git am`, `git commit-tree` or a shell alias.
  - Commits in a checkout that sets `git config omp.sessionTrailer false`.
  - Commits an older omp wrote itself, from before it read `PI_COMMIT_TRAILERS`.
  - `git merge`, `pull`, `cherry-pick` or `revert` commits older than the `shims/git` change that stamps them.
    - `git -C ~/.dotfiles log --reverse -S 'cherry-pick | revert' --format=%cI -- shims/git | head -1` prints the time of that `shims/git` change.
- Attribute those commits by author email and commit time, plus a session that edited the same files between the parent commit's time and the commit's time.
  - `git show -s --format=%cI <sha>^ <sha>` prints both times.
  - Then run `tq search --commands '<file>' --from '<parent time>' --to '<commit time>'` (`using-transcript-store`).
- Use the `jj` lines only where `jj root` succeeds.

```bash
git log -1 --format=%B <sha> | grep Omp-Session
jj log -r <rev> --no-graph -T description | grep Omp-Session
gh api repos/<owner>/<repo>/issues/comments/<id> -q .body | grep -o '<!-- legion: .* -->'   # pulls/comments/<id> for an inline review comment
```

## Reach the session, live first: it can answer

1. **Live.** List sessions with `envoy_sessions`, then message one with `envoy_send`.
   - For a commit, `scripts/tell-head-session <owner/repo> <sha> <message>` messages the session its trailer names.
2. **Resume.** Run `omp --resume <id>` (an id prefix works) on the machine that holds the transcript.
3. **Read.** Run `ls ~/.omp/agent/sessions/*/*_<id>*.jsonl`. The directory of the same name holds:

   | Path | One for each |
   | --- | --- |
   | `<AgentName>.jsonl` | Subagent. Its brief is the `task` field of the `session_init` record near the top |
   | `local/<name>` | `local://<name>` file the session wrote |

   - Files the session left in an agent box's home or `/tmp` died with the box.

## List what a session touched

- Run the `gh search` line with `issues` and again with `prs`: `issues` skips PR comments.

| `gh api` endpoint, for one repository | Sweeps |
| --- | --- |
| `issues/comments` | Conversation comments on issues and PRs |
| `pulls/comments` | Inline review comments |

- For review bodies, read `pulls/<n>/reviews` for each PR: they have no repository-wide endpoint.

```bash
git log -n <N> --format='@@@%h %s%n%B' <ref> | awk -v id='<id>' '/^@@@/{h=substr($0,4); next} $0 ~ ("^Omp-Session: " id) {print h}' | sort -u
jj log --no-graph -r 'description(glob:"*Omp-Session: <id>*")' -T 'commit_id.short() ++ " " ++ description.first_line() ++ "\n"'
gh search issues "<id> in:comments" --owner <owner>   # again with prs
gh api "repos/<owner>/<repo>/issues/comments" --paginate -q '.[] | select(.body | contains("<id>")) | .html_url'   # again with pulls/comments
```

- The `git log` line matches the whole message, not `%(trailers)`: a merge-queue squash appends a `Co-authored-by` paragraph after the `Omp-Session` lines, and git reads trailers from the last paragraph only.
- Keep the `git log` read bounded by `-n`.
- Add no `--grep` or `--all` to the `git log` read, which some repositories forbid.
- In a jj checkout, `git log --all` also counts hidden copies (`refs/jj/keep/*`).
- Widen `-n` until the last commit the `git log` read prints is older than the session's start.
  - `git log -n <N> --format=%cI <ref> | tail -1` prints that commit's time.
  - The stamp before `_<id>` in the transcript's file name (the `ls` of step 3 above) is the session's start.
- A merge commit from a merge queue that uses the `MERGE` method has an `Omp-Session` line only if the PR body did.
- For such a merge commit, use `<merge>^1..<merge>^2` as `<ref>`: the branch's commits keep their `Omp-Session` lines.

## Read the evidence

- jj adds the trailer when a commit is described or committed.
- In a working copy several sessions share (`~/.dotfiles`, a canonical `~/src/<repo>`), the trailer names the session that described the commit, not the author of each file.
- Treat the trailer as the author only in a per-session workspace.
- Outside a per-session workspace:
  - Compare the commit's files (`git show --stat <sha>`) with what each candidate edited.
  - Ask the candidates.
  - Split by file when owners claim files (`jj split -r <rev> -m '<message>' <paths>`).
- Skill or slash-command text in a transcript does not prove the session invoked it.

  | That text in | Is |
  | --- | --- |
  | A `message` record whose role is `user` or `fileMention` | An invocation |
  | A `toolResult` or `compaction` record | A file the session read |
