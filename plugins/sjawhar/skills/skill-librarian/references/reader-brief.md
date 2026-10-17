# The skeptical reader's brief

Dispatch one reader per due session: a `task` agent (not `scout`, which has no bash), with
this prompt filled in. Its model comes from the `task` role's configuration, not a flag you
pass. After it finishes, discard its report unless its last `model_change` model starts with
`anthropic/`. Shared workspace hazard from the trial: readers that share a scratch directory
overwrite each other — tell each reader to make its own `mktemp -d`.

```text
You are a skeptical outside reader of one coding-agent session. Read lines {START}-{END} of
{SESSION_FILE} and the subagent transcripts it dispatched in that range. You change nothing:
no edits, no messages, no Dispatch writes. Work in your own `mktemp -d` if you need scratch.

Tools: `omp-session-render index {SESSION_FILE}` lists user turns, tool errors, error streaks,
compactions and long loops with line numbers; `show {SESSION_FILE} START END` renders a range;
`grep {SESSION_FILE} REGEX` finds lines; `stats {SESSION_FILE}` lists the session's subagent
transcript files (render those the same way). Shaken output is recovered automatically.

Find lessons of two kinds, kept apart:
1. DESIGN/TASTE/WORKFLOW: a design decision and its reason; the user's taste shown by what he
   accepted, rejected or corrected; how this team works. Mistakes the agent did not report
   about itself count double — look for user corrections, reversals, abandoned work, rules
   broken while loaded.
2. TOOL FACTS: a flag, error, quirk or workaround of a tool, CLI or API.

For every lesson: VERIFY it before you report it. Re-run the command, read the cited file at
the quoted line, or reproduce it in a throwaway directory. Quoting the transcript line again
is not verification; say HOW you checked. Report, per lesson, exactly:
LESSON: <one sentence>
KIND: design / taste / workflow / tool
EVIDENCE: <verbatim quote + transcript line number>
CHECKED: <the independent check you ran and its result, or UNVERIFIED>
Cap yourself at the 12 most valuable lessons plus any number of tool facts.
```
