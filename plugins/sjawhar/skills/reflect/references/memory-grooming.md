# Memory grooming

Read this at the end of a run, after the report. Grooming is bookkeeping over the memory banks; doing it before the findings are written takes the hours the reading needs.

Banks: `~/.omp/agent/memories/mnemopi/mnemopi.db` (global) and `banks/*/mnemopi.db` (per project). `memory_edit` reaches only the global bank and the current project's. Open any other bank read-only, and list it in the report for grooming from a session in its own project.

1. Find candidates with read-only SQLite (`working_memory`, `facts`, and rows with a low `veracity` value). Change memory only through `memory_edit`, never SQL: SQL writes skip the index the recall path reads.
2. `recall` this window's correction topics, user preferences, project decisions and tooling facts.
3. Near-duplicates: keep the best-worded one. `invalidate` the rest with `replacement_id` set to it.
4. Contradictions: decide by recency and by transcript evidence. Read the full `memory://<id>` first, because a preview is truncated. `update` the survivor, `invalidate` the losers.
5. A memory that states as general a decision he made for one task: re-scope it with `update`, or invalidate it. Invalidate point-in-time status that is no longer true (a PR "merge-ready", a pin, "blocked on X").
6. `forget` only pure noise with no historical value. Otherwise `invalidate`, so the history stays readable.
7. Return a ledger: each id, the operation and a one-line reason. It goes in the report under "Memory grooming".

End the report by suggesting that Sami run `/memory enqueue`, so the window's facts reach the banks the next run reads.

More than 20 changes in one run: present the list and wait for approval. If the last report carries no "Memory grooming" ledger, list the changes and apply none, because no run has had this approved yet.
