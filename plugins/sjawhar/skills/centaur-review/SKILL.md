---
name: centaur-review
description: "Use when Sami reviews someone else's pull request with you: he narrates his observations as he reads the diff, and the review goes to GitHub as inline review comments."
args: "<pr_url_or_number>"
---

# Centaur Review

**Done when:** you report `.html_url` from the response to the §4 POST.

**Sami leads:** show your analysis or the agents' findings only when he asks, or at §3 step 4. Unasked analysis
replaces his judgment.

In a jj checkout, pass the repository explicitly (`gh -R <owner>/<repo> ...`). Do not export `GIT_DIR` or
`GIT_WORK_TREE`: they send unrelated git subprocesses, test fixtures too, into the shared repository.

## 1. Set up

1. **Do not start without a PR URL or number:** do not guess which PR.
2. **Fetch the PR,** its diff, its linked issues, and all reviews and comments:

   ```bash
   REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)   # jj checkout: REPO=<owner>/<repo>
   gh pr view <pr> -R "$REPO" --json number,title,body,url,headRefName,baseRefName,headRefOid,files
   gh pr diff <pr> -R "$REPO"
   gh api repos/$REPO/pulls/<number>/reviews
   gh api repos/$REPO/pulls/<number>/comments
   gh pr view <pr> -R "$REPO" --comments
   ```

3. **Show Sami a summary** if he has already reviewed this PR: open requests, unanswered questions, unresolved
   threads, changes since his review, and other reviewers' concerns, before he starts reading. The summary reports
   the state of the review on GitHub, not your analysis of the code.
4. **Start these agents in parallel** with the `task` tool:

   | Agent | Looks for |
   | --- | --- |
   | `bug-finder` | Correctness, security, concurrency and edge-case defects that change what the code does |
   | `code-simplifier` | Complexity or abstraction a smaller change removes |
   | `code-architect` | Structural effects, pattern drift, cross-module effects |
   | `code-reviewer` | Omissions, scope creep and deviations the linked issue does not allow |

   - Keep their results until §3.

## 2. Track the narration

- Keep a private tracker: `# | file:start_line-end_line | severity | observation`.
- Find each location from an explicit reference or the diff.
- If his quote or concept matches no place or several, ask Sami which file or line: a misplaced comment misleads the
  author.
- Use new-file lines for additions, old-file lines for deletions.
- Acknowledge each observation in one line, so he keeps reading.
- When he asks, show the tracker, show your analysis, or drop the last item.
- Take the severity from his words:

  | Severity | His words |
  | --- | --- |
  | BLOCKING | blocking, must fix, cannot merge |
  | IMPORTANT | important, should fix, concern |
  | SUGGESTION | suggestion, could, maybe, idea |
  | QUESTION | question, wondering, why |
  | NITPICK | nitpick, minor, tiny |

## 3. Consolidate and write

When Sami ends his narration:

1. **Open each tracked `file:start_line-end_line`** in the diff and confirm the lines show the observation. Ask Sami
   about each that does not.
2. **Ask the `red-teamer` agent to challenge each comment,** not to review the PR: is it right, does it miss context,
   will it improve the code?
3. **Collect the four agent results** from §1 step 4. Discard each that reports no finding.
4. **Show Sami his comments,** the challenges and the four agents' findings. He decides what stays.
5. **Write each kept comment** as a clear observation, then the concrete thing it asks of the author:

   | Severity | The thing it asks |
   | --- | --- |
   | QUESTION | A question |
   | Every other severity | The change, or a question when Sami named no change |

   - Open with the observation, not praise or a hedge.
   - No emojis.

   ```markdown
   **Suggestion:** The caller can supply an empty list, so indexing `items[0]` would
   raise here. Could we return early when `items` is empty?
   ```

6. **Write the review body** (`$SUMMARY` in §4) after the inline comments: `## Review Summary`, then two to five
   sentences that give Sami's overall assessment and name the BLOCKING and IMPORTANT comments, if any.
   - Do not repeat every inline comment: each one already sits on its line.
7. **Post only after Sami explicitly approves:** the review goes out under his name.

## 4. Post the review

Post to the reviews endpoint with the review as JSON on stdin. Do not build `comments` from `-f`/`-F` fields: gh
sends every `-F` field after every `-f` field, so two comments get their fields mixed up.

```bash
COMMENT1=$(cat <<'EOF'
**Suggestion:** `items` can be empty here. Could we return early?
EOF
)
head=$(gh pr view <pr> -R "$REPO" --json headRefOid -q .headRefOid) &&
review=$(jq -n --arg body "$SUMMARY" --arg commit_id "$head" --arg c1 "$COMMENT1" '{
  body: $body, event: "COMMENT", commit_id: $commit_id,
  comments: [
    {path: "src/example.py", line: 42, body: $c1}
  ]
}') && gh api repos/$REPO/pulls/<number>/reviews --method POST --input - <<<"$review"
```

- Each comment needs `path` (as in the diff), `line` (a number, in the new file) and `body`.
- A comment on a deleted line also needs `side: "LEFT"`, with `line` in the old file.
- Set each body, `$SUMMARY` too, with a quoted heredoc like `COMMENT1` and pass it with `--arg`, so quotes,
  backticks and backslashes reach GitHub unchanged.
- On `"line" is not a permitted key`, use `/pulls/{number}/reviews`, not `/comments`: that error means the wrong
  endpoint.
- On HTTP 422 or a missing comment, recheck `path`, `line`, `side` and `body` against the diff.
- If the POST fails, show Sami the status and error.
- If the POST fails, post a top-level PR comment only if he asks: it loses each comment's line.
