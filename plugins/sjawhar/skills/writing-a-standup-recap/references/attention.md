# The attention map

Where Sami's own attention went in the window, counted from what he wrote. It goes to him alone, in the same
message as the recap link or just after it, never on the team page.

## What to count

- His writes in Dispatch: comments, ask answers, messages. Read them with the `dispatch_rows` function of
  `~/.dotfiles/scripts/sami-said`, imported; running the whole tool takes longer than the recap allows. A message
  sent to several sessions is one Dispatch event per copy: count it once.
- His typed turns to agents: the rows of `~/.omp/agent/history.db` in the window. Profiles share one database, so
  read one. Drop a text whose first line names an agent or a script, and drop drill and probe sessions.
- Leave quoted lines (`> ...`) out of the word count: they are other people's words.

## What to report

1. Share of his written items and of his words, never "time spent": these records do not measure reading, meetings
   or duration.
2. One table by project in his priority order, with the projects that are not priorities (managing agents; people
   and outside parties) below it. Items you could not classify get their own row. A priority with no classifier
   match is unmeasured, not zero attention: say which it is.
3. At most three observations, each one the numbers support, and the three to five topics that took the most words,
   each with one link.
4. What is not counted: Slack, email, meetings, voice, GitHub comments, other machines. A correction typed into a
   project's own session counts under that project, so "managing agents" is a lower bound. `history.db` keeps one
   row per distinct text, so a repeated prompt counts once.
5. The item rows in a file, and the command that rebuilds the table from it.
