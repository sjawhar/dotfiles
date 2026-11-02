# The attention map

Where Sami's own attention went in the window, counted from what he wrote. It goes to him alone, in the same
message as the recap link or just after it, and never on the page the team reads.

## What to count

- His writes in Dispatch: comments, ask answers, messages. Deduplicate by Dispatch id; count a message sent to
  many sessions once.
- His typed turns to agents: rows of `~/.omp/agent/history.db` in the window. Drop a text whose first line names
  an agent or a script, and drop drill and probe sessions. Profiles share one database, so read one.
- Read the Dispatch events with the `dispatch_rows` function of `~/.dotfiles/scripts/sami-said`, imported, rather
  than running the whole tool, which takes longer than the recap allows; read the typed turns from the history
  database yourself.
- Count one message he sent to several sessions once, because each copy is its own Dispatch event.
- Leave quoted lines (`> ...`) out of the word count, because they are other people's words.

## What to report

Share of his written items and of his words, never "time spent", because these records do not measure reading,
meetings or duration. One table by project in his priority order, with the projects that are not priorities
(managing agents; people and outside parties) below it. Items you could not classify get their own row, and a
priority with no classifier match is unmeasured, not zero attention: say which it is. Then at most three
observations, each one the numbers support, and the three to five topics that took the most words, each with one
link.

End with what is not counted: Slack, email, meetings, voice, GitHub comments, other machines; a correction typed
into a project's own session counts under that project, so "managing agents" is a lower bound; `history.db` keeps
one row per distinct text, so a repeated prompt counts once.

Write the item rows to a file and give the command that rebuilds the table from it, because he checks totals.
