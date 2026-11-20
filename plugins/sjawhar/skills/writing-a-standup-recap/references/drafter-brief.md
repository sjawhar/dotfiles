# Brief for a stream drafter

Write this brief, blanks filled, to one shared file and point every drafter at it; give each drafter only its
stream, its sources and its deadline in the task. Set the deadline 20 to 25 minutes out: a checked partial draft on
time beats a full one late.

```markdown
# Recap draft: <stream>, <window start> to now (Pacific)

## Readers
The people of Theorem: engineers on other projects and staff who are not technical. Not agents. They care what
Theorem has now that it did not have at <window start>, and why that matters to the company.

## Sources, in this order
1. The stream's records on its stream issue <KEY>: `stream.md` (its ledger: merged, measured, done), the latest
   `summary.md`, and `scoreboard.json` (`dispatch read --issue <KEY> --artifact <name>`).
2. Merged pull requests in the shared file <path> (fields: repo, number, title, user, merged_at, url, labels, body).
   It is an index, not evidence: each body is cut to 600 characters, so open the pull request
   (`gh pr view <n> -R <owner>/<repo>`) before you rest a claim on it. Run no broad GitHub search: the machine
   shares one rate limit.
3. Releases, closed issues and result documents that the records link.
4. The stream's lead: ask what it delivered and what you have missed.
Read every number at its source, never from a message or a progress line. If you cannot check it, leave it out and
say so at the end.

## Output: <path>, by <deadline>
Up to 5 items, most important first. Each item:
- a headline: the result for Theorem in plain words;
- why it matters to Theorem, one sentence;
- the date in Pacific, with "weekend" for Saturday and Sunday;
- the number with its comparison (before -> after, out of how many, or against the target), what it measures,
  over what period, and its source;
- 1 to 3 links: pull request or release URL with its title, Dispatch `https://` link.
Then: "Chart data" as CSV with its source; "Next this week", at most 2 lines, only deliveries with a date;
"Glossary" for each term a reader who is not technical needs; "Left out, and why"; "Not checked".

## What counts
- Delivered means merged, released, measured or decided in the window, with a link that proves it.
- Group many pull requests into one result. A count of pull requests is not a result.
- Agent tooling, skills, prompts and the agents' own process count only if a person at Theorem feels the effect.
- Work a named person did on their own is theirs. Fixes to our own mistakes are not results.
- A number that went down needs its reason. A measure whose definition changed in the window is not a trend.
```
