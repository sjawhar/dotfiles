---
name: centaur-review
description: "Use when Sami reviews someone else's pull request with you. He narrates his observations as he reads the diff, and the result goes to GitHub as inline review comments."
args: "<pr_url_or_number>"
---

# Centaur Review

Sami leads: show your analysis or the agents' findings only when he asks, or at §3 step 4. Unasked analysis replaces his judgment.

In a jj checkout, pass the repository explicitly (`gh -R <owner>/<repo> ...`). Never export `GIT_DIR` or `GIT_WORK_TREE`: they send unrelated git subprocesses, test fixtures too, into the shared repository.

## 1. Set up

1. Without a PR URL or number, do not start: never guess which PR.
2. Fetch the PR, its diff, its linked issues, and all reviews and comments:

   ```bash
   REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)   # jj checkout: REPO=<owner>/<repo>
   gh pr view <pr> -R "$REPO" --json number,title,body,url,headRefName,baseRefName,headRefOid,files
   gh pr diff <pr> -R "$REPO"
   gh api repos/$REPO/pulls/<number>/reviews
   gh api repos/$REPO/pulls/<number>/comments
   gh pr view <pr> -R "$REPO" --comments
   ```

3. If Sami has already reviewed this PR, summarize open requests, unanswered questions, unresolved threads, changes since Sami's review, and other reviewers' concerns.
4. Start these agents in parallel with the `task` tool. Keep their results until §3.

   | Agent | Finds |
   | --- | --- |
   | `bug-finder` | correctness, security, concurrency and edge-case defects that change what the code does |
   | `code-simplifier` | complexity or abstraction a smaller change removes |
   | `code-architect` | structural effects, pattern drift, cross-module effects |
   | `code-reviewer` | omissions, scope creep and deviations the linked issue does not allow |

## 2. Track the narration

- Keep a private tracker: `# | file:start_line-end_line | severity | observation`.
- Find each location from an explicit reference or the diff. If his quote or concept matches no place or several, ask Sami which file or line. Never guess: a misplaced comment misleads the author.
- Use new-file lines for additions, old-file lines for deletions.
- Acknowledge each observation in one line, so he keeps reading.
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

1. Open each tracked `file:start_line-end_line` in the diff and confirm the lines show the observation. Ask Sami about each that does not.
2. Ask the `red-teamer` agent to challenge each comment, not to review the PR: is it right, does it miss context, will it improve the code?
3. Collect the four agent results from §1 step 4. Discard each that reports no finding.
4. Show Sami his comments, the challenges and the four agents' findings. He decides what stays.
5. Write each kept comment as a clear observation, then a concrete question or change where one fits. Open with it, not praise or a hedge, so the author sees the point. No emojis.

   ```markdown
   **Suggestion:** Could we return early when `items` is empty? The caller can supply
   an empty list, so indexing `items[0]` would raise here. Let me know if I missed an
   invariant.
   ```

6. After the inline comments, write a concise summary that starts with `## Review Summary`.
7. Post only after Sami explicitly approves: the review goes out under his name.

## 4. Post the review

Post to the **reviews** endpoint (`repos/$REPO/pulls/<number>/reviews`) with the review as JSON on stdin: `gh`'s `-f`/`-F` fields mix up the fields of two comments. The exact command, the per-comment keys and the errors are in `references/posting-a-review.md`. Report `.html_url` from the response.
