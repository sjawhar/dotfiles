---
name: centaur-review
description: "Use when Sami reviews someone else's pull request with you. He narrates his observations as he reads the diff, and the result goes to GitHub as inline review comments."
args: "<pr_url_or_number>"
---

# Centaur Review

Sami leads. Do not replace his judgment with analysis he did not ask for.

In a jj checkout, pass the repository explicitly (`gh -R <owner>/<repo> ...`). Never export `GIT_DIR` or `GIT_WORK_TREE`: they send unrelated git subprocesses, test fixtures too, into the shared repository.

## 1. Set up

1. Without a PR URL or number, do not start.
2. Fetch the PR, its diff, its linked issues, and all reviews and comments:

   ```bash
   REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
   gh pr view <pr> --json number,title,body,url,headRefName,baseRefName,headRefOid,files
   gh pr diff <pr>
   gh api repos/$REPO/pulls/<number>/reviews
   gh api repos/$REPO/pulls/<number>/comments
   gh pr view <pr> --comments
   ```

3. For a re-review, summarize open requests, unanswered questions, unresolved threads, changes since Sami's review, and material concerns from other reviewers.
4. Start these agents in parallel with the `task` tool. Keep their results until step 3.

   | Agent | Finds |
   | --- | --- |
   | `bug-finder` | significant correctness, security, concurrency, edge-case defects |
   | `code-simplifier` | consequential needless complexity or abstraction |
   | `code-architect` | structural effects, pattern drift, cross-module effects |
   | `code-reviewer` | omissions, scope creep and material deviations from the linked issue |

## 2. Track the narration

- Keep a private tracker: `# | file:start_line-end_line | severity | observation`.
- Find each location from an explicit reference or the diff. If a quote or concept is ambiguous, ask Sami which file or line. Never guess.
- Use new-file lines for additions, old-file lines for deletions.
- Acknowledge each observation briefly, without interrupting his flow.
- When he asks, show the tracker, show your analysis, or drop the last item.

| Severity | His words |
| --- | --- |
| BLOCKING | blocking, must fix, cannot merge |
| IMPORTANT | important, should fix, concern |
| SUGGESTION | suggestion, could, maybe, idea |
| QUESTION | question, wondering, why |
| NITPICK | nitpick, minor, tiny |

## 3. Consolidate and write

When Sami ends his narration:

1. Check every tracked location.
2. Ask the `red-teamer` agent to challenge each comment, not to review the PR: is it right, does it miss context, will it make the code better?
3. Collect the four results from step 1. Discard each "Nothing to add".
4. Show Sami his comments, the challenges and the candidate findings. He decides what stays.
5. Write each kept comment as a clear observation, then a concrete question or change where one fits. Be friendly and direct. Put clarity and actionability before softening. No emojis in place of precision.

   ```markdown
   **Suggestion:** Could we return early when `items` is empty? The caller can supply
   an empty list, so indexing `items[0]` would raise here. Let me know if I missed an
   invariant.
   ```

6. After the inline comments, write a concise summary that starts with `## Review Summary`.
7. Post only after Sami explicitly approves.

## 4. Post the review

Post to the **reviews** endpoint with the review as JSON on standard input. Do not build `comments` from `-f`/`-F` fields: gh adds every `-F` field after every `-f` field, so a review with two comments gets their fields mixed up.

```bash
jq -n --arg body "$SUMMARY" \
  --arg commit_id "$(gh pr view <pr> -R "$REPO" --json headRefOid -q .headRefOid)" \
  --arg c1 '**Suggestion:** Could we handle the empty case here?' '{
  body: $body, event: "COMMENT", commit_id: $commit_id,
  comments: [
    {path: "src/example.py", line: 42, body: $c1}
  ]
}' | gh api repos/$REPO/pulls/<number>/reviews --method POST --input -
```

- Each comment needs `path` (as in the diff), `line` (a number, in the new file) and `body`. A comment on a deleted line also needs `side: "LEFT"`, with `line` in the old file. Pass each body with `--arg`, so that its quotes and backslashes reach GitHub unchanged.
- Never fall back silently to a top-level PR comment.

| Error | Remedy |
| --- | --- |
| `"line" is not a permitted key` | Wrong endpoint: use `/pulls/{number}/reviews`, not `/comments`. |
| HTTP 422 or a missing inline comment | Check each comment's `path`, `line` and `body`, the diff path and the new-file line (for a deleted line, `side: "LEFT"` and the old-file line). |

Report the review URL that GitHub returns.
