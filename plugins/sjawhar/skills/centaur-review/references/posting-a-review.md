# Posting the review

Post to the **reviews** endpoint, the review as JSON on stdin. Do not build `comments` from `-f`/`-F` fields: gh adds every `-F` field after every `-f` field, so a review with two comments gets their fields mixed up.

```bash
COMMENT1=$(cat <<'EOF'
**Suggestion:** Could we handle the empty case here?
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

- Each comment needs `path` (as in the diff), `line` (a number, in the new file) and `body`. A comment on a deleted line also needs `side: "LEFT"`, with `line` in the old file. Set each body, `$SUMMARY` too, with a quoted heredoc like `COMMENT1` and pass it with `--arg`, so quotes, backticks and backslashes reach GitHub unchanged.
- If the POST fails, show Sami the status and error. Post a top-level PR comment only if he asks: it loses each comment's line.
- `"line" is not a permitted key` means wrong endpoint: use `/pulls/{number}/reviews`, not `/comments`. On HTTP 422 or a missing comment, recheck `path`, `line`, `side` and `body` against the diff.

Report `.html_url` from the response.
