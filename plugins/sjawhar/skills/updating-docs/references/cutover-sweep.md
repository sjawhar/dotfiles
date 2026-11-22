# On a cutover, sweep the changed directory

1. **List the files.** Use a tree walk (`git ls-files <dir>`), not a hand-written glob, which misses nested paths.
   - Check every file type: scripts and configs record what runs.
2. **Write the questions.** Before you sweep, write down the questions each file must answer (stale claim? resolved?); without them you check only what you happen to notice.
3. **Have a second agent sweep** when a decision rests on the sweep (a deletion, a merge, or a report that no stale copy is left).
   - Have it sweep from the claim itself, not your search terms.
   - Merge the hits.

   | Harness | Start the second agent with |
   | --- | --- |
   | omp (oh-my-pi, the agent harness), which sets `OMPCODE=1` in every shell it starts | The `task` tool and `agent: "scout"`, a read-only search agent |
   | Another harness | A read-only subagent |
