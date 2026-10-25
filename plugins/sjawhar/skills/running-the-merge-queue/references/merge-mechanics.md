# Protected merge and literal evidence writes

Read before you post evidence or merge. Use the current approved identity, never a reused personal token or an admin path. Load the skills for each tool before you use it, for example `dispatch` before a Dispatch write and `using-secrets` before you use a credential.

## Gate, then merge pinned

- Refresh authority, owner completion, head and branch identity, CI, findings and evidence, all for one SHA.
- CI: the required aggregate and required lanes are green at the head. Identify superseded cancelled runs by run ID. "Expected red" and in-progress are not green.
- Check mergeability, declared holds and the intended base separately; one green result says nothing about the others.
- Scope: compare the packet's files and count with the paginated PR files API, and its commits with the PR commits API, not local `HEAD`. Return an unexplained scope increase or decrease to the owner. The remote branch tip must match the PR's head association; until the intended head is clear, do not merge.
- The PR names its Dispatch issue in title, body, branch or commit message, including PRs merged under an outage exception. In a public repository, use the bare key, never an internal link. Fix a missing key in the body, without a push.
- Establish every gate in SKILL.md, and the files API match on every page, for the same intended SHA.
- The gate-and-merge path re-reads checks and unresolved findings and refuses on failure. Never run a census and a merge unconditionally, then read the output after. A check that prints DIFFERS and exits 0 is not a gate. Chain dependent commands with `&&`, never with a semicolon or a newline.
- Use the repository's protected merge tool if it has one. The ordinary CLI form, as the final operation:

```bash
gh pr merge N --repo O/R --squash --match-head-commit <verified-sha> \
  --subject '<squash title>' --body-file <merge-body-file>
```

- An API merge sets `commit_title`, `commit_message`, head `sha` and the squash method.
- If the head changes, GitHub must refuse. Refresh the packet and evidence. Never retry unpinned.
- Native stacks use their own protected path: [sequencing](sequencing.md).

## Write the record literally

- The squash title and message carry the verified head, CI run links, review and acceptance artifacts, and justified review reuse. They also carry any scoped waiver and the named follow-up destinations. An APPROVE body is not the squash record.
- Write prose to a file with the file-write tool or a quoted heredoc. Never pass backticks or `$` through a double-quoted shell string. Use `--body-file` where offered. In `gh api`, `-F body=@<file>` reads the file and `-f body=@<file>` posts the filename.
- A 201 and a valid URL do not prove the body. Read it back from the API. Edit a wrong comment in place. Do not delete and repost.
- For a batched GraphQL reply-and-resolve, check both mutation results and the resulting reply and thread state.
- After merge, read the actual message. The intended head must have merged, and the evidence must be in the message: `gh api repos/O/R/commits/<merged-sha> --jq .commit.message`.

## Repost another agent's report

- Extract the exact turn. In a transcript, stop at the next role header (`## developer`, `## assistant`), not a fixed line count. Check the saved text for role headers and harness reminders before you post.
- Backticks do not stop a bot mention matched as raw text. Check the repository's comment-trigger rules, then break the mention token or name the bot in prose. A repost must not launch a review round.

## Credentials and borrowed terminals

- Check routing and the actual account for each write path. One tool can post as different identities in different environments.
- Use a repository's one-time exemption only when you can link the ruling that grants it for this PR; it is not a general bypass.
- A missing credential, self-approval restriction or absent CODEOWNER review is a blocker. It is never permission to disable protection or reuse a token from a prior session.
- If Sami explicitly supplies a tmux pane for an authorized action, find it each time by its current content, cwd and command. Never use a remembered index: `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_current_path}'`.
- A missing or ambiguous pane needs a fresh authorized route. Never type into a pane you have not just identified. A one-command credential grant is not standing queue authority.

## Deployment read-back

- Use `post-merge` and the repository's deployment contract. Check commit statuses as well as check-runs when they control delivery. A merged PR is not a deployment.
- A provider can reject deployment for an unconnected external squash author after GitHub merged. Name the responsible owner and use the remedy the repository's deployment docs approve: a manual deploy, or one triggered by the next commit from a connected author. Never connect accounts to bypass it.
