---
name: centaur-review
description: "Use when reviewing someone else's PR together — Sami narrates his review and wants comments tracked, challenged, and posted as inline GitHub review comments."
args: "<pr_url_or_number>"
---

# Centaur Review

Sami leads the review by narrating as he reads. Track his observations, find their
exact diff locations, challenge weak suggestions after he finishes, and compose a
friendly review together. Do not replace his judgment with unsolicited analysis.

> **jj workspace note:** You may be in a non-default jj workspace with no `.git`
> directory. If `gh` fails, set `GIT_DIR` to the default workspace's `.git` directory.

## Set up the review

Require a PR URL or number. Fetch its metadata, changed files, diff, linked issues,
and existing reviews, line comments, and conversation comments:

```bash
gh pr view <pr> --json number,title,body,url,headRefName,baseRefName,headRefOid,files
gh pr diff <pr>
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
gh api repos/$REPO/pulls/<number>/reviews
gh api repos/$REPO/pulls/<number>/comments
gh pr view <pr> --comments
```

For a re-review, summarize outstanding requests, unanswered questions, unresolved
threads, changes since Sami's review, and material concerns from other reviewers.

Launch these agents in parallel and hold their results until consolidation:

- **bug-finder** — significant correctness, security, concurrency, and edge-case defects.
- **code-simplifier** — consequential unnecessary complexity or avoidable abstraction.
- **code-architect** — structural implications, pattern drift, and cross-module effects.
- **code-reviewer** — compare the diff with linked issue requirements; identify omissions,
  scope creep, and material deviations.

## Narrated review

Keep a private tracker: `# | file:start_line-end_line | severity | observation`.
Resolve locations from explicit references or the diff; if a quote or concept is
ambiguous, ask which changed file or line Sami means. Never guess a location.

Briefly acknowledge each observation without interrupting flow. On request, show the
tracker, show analysis, or drop the last item. Interpret severity words as follows:

| Label | Signals |
| --- | --- |
| BLOCKING | blocking, must fix, cannot merge |
| IMPORTANT | important, should fix, concern |
| SUGGESTION | suggestion, could, maybe, idea |
| QUESTION | question, wondering, why |
| NITPICK | nitpick, minor, tiny |

Use actual new-file line numbers for additions and old-file lines for deletions.

## Consolidate and write

When Sami says he is done, verify every tracked location. Ask the **red-teamer** to
challenge each comment—not to review the PR—checking whether it is correct, has
missing context, or would improve the code. Gather the four background results,
discarding “Nothing to add,” then show Sami his comments, challenges, and candidate
findings. He decides what remains.

Write each retained inline comment with a clear observation, a concrete question or
change when appropriate, and a friendly, direct tone. Clarity and actionability come
before softening; do not use emojis as a substitute for precision. Example:

```markdown
**Suggestion:** Could we return early when `items` is empty? The caller can supply
an empty list, so indexing `items[0]` would raise here. Let me know if I missed an
invariant.
```

Write a concise overall summary after the inline comments. Ask for explicit approval
before posting.

## Post inline review comments

Get the PR head SHA and post to the **reviews** endpoint. Use `-f` array notation:

```bash
COMMIT_SHA=$(gh pr view <pr> --json headRefOid -q '.headRefOid')
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')

gh api repos/$REPO/pulls/<number>/reviews \
  --method POST \
  -f body="## Review Summary\n\n<concise assessment>" \
  -f event="COMMENT" \
  -f commit_id="$COMMIT_SHA" \
  -f 'comments[0][path]=src/example.py' \
  -f 'comments[0][line]=42' \
  -f 'comments[0][body]=**Suggestion:** Could we handle the empty case here?' \
  -f 'comments[1][path]=src/other.py' \
  -f 'comments[1][line]=89' \
  -f 'comments[1][body]=**Question:** What invariant makes this safe?'
```

Each `comments[N]` needs only `path`, `line`, and `body`; `path` must match the diff
and `line` must exist in the new file. Do not silently fall back to a top-level PR
comment: the purpose is line-level feedback.

| Error | Cause and correction |
| --- | --- |
| `"line" is not a permitted key` | Wrong endpoint. Use `/pulls/{number}/reviews`, **not** `/pulls/{number}/comments`. |
| HTTP 422 | Check the exact `comments[0][path]`, `[line]`, and `[body]` array notation. |
| Inline comment missing | Confirm the precise diff path and a valid line in the new file. |

Confirm the resulting review URL after GitHub accepts the request.
