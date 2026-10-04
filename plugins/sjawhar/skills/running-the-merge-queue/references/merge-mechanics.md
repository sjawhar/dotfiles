# Protected merge and literal evidence writes

Read this before posting evidence or merging. Use the current approved identity,
never a reused personal token or an admin path. Load the applicable
posting/credential skills before using their tools.

## Fresh verification, then a pinned protected merge

Before any merge, establish all of these for the same intended SHA:

- current authority, priority/holds and actual work-owner completion;
- gate-lane READY packet where required;
- remote branch tip matching the PR's head association and intended base;
- packet file list/count matching every page of the PR files API;
- green required checks, actual mergeability, every finding's disposition;
- independent verdicts, required acceptance and oracle coverage.

The gate-and-merge path must re-read checks and unresolved findings and refuse on
failure. Do not run “census; merge” and only then inspect the output. A check that
prints DIFFERS but exits successfully is not a safe gate. Read the verification
result before continuing, and chain any dependent successful commands with `&&`,
not semicolons or unconditional newlines.

Use the repo's existing protected merge tool when available. The ordinary CLI
shape must pin the verified head, for example:

```bash
gh pr merge N --repo O/R --squash --match-head-commit <verified-sha> \
  --subject '<squash title>' --body-file <merge-body-file>
```

This command is the final operation, not a replacement for the preceding gate.
Never add an admin bypass. If the head changes, GitHub must refuse; refresh the
packet and evidence rather than retry unpinned. Native stacks use their own
protected path as described in [sequencing](sequencing.md).

## Write the permanent record

The squash title/message names the verified head, required CI/run links, review
and acceptance artifacts, justified evidence reuse, any scoped waiver and each
non-blocking follow-up destination. An APPROVE comment is not that commit message.
If using a supported API merge path, supply its actual `commit_title` and
`commit_message` fields as well as the head `sha` and squash method.

Write prose to a file literally, using the file-write tool or a quoted heredoc.
Do not interpolate backticks or dollar signs through a double-quoted shell string.
Use `--body-file` where provided. For `gh api`, `-F body=@<file>` reads a file;
`-f body=@<file>` posts the literal filename. A 201 and valid URL do not prove the
body was correct.

Read the posted body back from its API. For a comment, inspect the returned ID and
its body; if wrong, edit that same comment in place rather than delete/repost.
For a batched GraphQL reply-and-resolve, inspect both mutation results and the
resulting reply and thread state. One can succeed while the other fails.

After merge, read the actual message:

```bash
gh api repos/O/R/commits/<merged-sha> --jq .commit.message
```

Confirm the PR merged the intended head and the recorded evidence reached the
squash message. Do not merely assert that a script was designed to write it.

## Safely repost another agent's report

Extract the report's exact turn. If it lives in a transcript, stop at the next role
header such as `## developer` or `## assistant`, not at a fixed line count after a
heading. Inspect the saved text for role headers and harness reminders before
posting. Do not publish the next turn's injected content with the report.

Check the repository's comment-trigger rules. Backticks do not neutralize a bot
mention matched as raw text. When quoting a trigger without requesting its action,
break the mention token or refer to the bot by name in prose. A report repost must
not silently launch another review round.

## Credentials and borrowed terminals

Verify routing and the actual account for each write path. The same tool can post
under different identities depending on its environment. A missing credential,
self-approval restriction or absent CODEOWNER review is a blocker, not permission
to disable protection or reuse a token from a prior session.

If Sami explicitly supplies a tmux pane for an authorized action, locate it by its
current content/cwd and command each time, never a remembered pane index:

```bash
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_current_path}'
```

A missing or ambiguous pane needs a fresh authorized route. Never type into a pane
you have not just identified. A one-command credential grant does not persist as
standing queue authority.

## Deployment read-back

Use `post-merge` and the repository's deployment contract. Check commit statuses as
well as check-runs when they control delivery. A merged PR is not a deployment.
Some providers reject deployments for an unconnected external squash author even
when GitHub merged successfully. Name the responsible owner and use the approved
manual/next-author deployment remedy; do not connect accounts to bypass it.
