# Close the loop

Read after each merge, and after each round with waits.

- Use `post-merge`. Name the production check, the log line, metric or output that shows the change working, and who runs it.
- When the merged PR has stacked children, tell their owners at once, as [sequencing](sequencing.md) says.
- After the production check passes, the owner runs `ce-compound`. It puts what `ce-compound` wrote for each gotcha that cost more than half an hour or contradicted the docs (`opening-a-pr` step 7) in a new docs-only PR before it starts other work, never in a push to the merged PR, which would restart its gates.
- After a round with waits, the owner reads its own transcript and posts on the work's Dispatch issue each wait on a check or reply it could have started earlier, so the next round starts it sooner. Do the same for your own work, in the queue record.
