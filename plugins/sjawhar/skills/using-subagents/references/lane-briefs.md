# Briefs by lane type

The lines each kind of lane needs on top of `SKILL.md`.

## Tester or acceptance lane

- Brief it skeptical: the work is broken until the tester shows it working on the surface the user runs. Unit-only verification does not count.
- On a defect, the tester writes the failing test itself. The resumed implementer's brief says "make the tester's red test pass; do not modify it", and the handoff marks tester-written tests as such.
- Ask for a verdict per item: RAN, BLOCKED or PARTIAL, never a bare pass or fail. PARTIAL means the check touched the path but asserted something adjacent, so it is not a pass. Carry each row's verdict into a re-dispatch.
- Give a stop condition, e.g. "if container creation hangs for more than 2 minutes, report that item BLOCKED with the command and what you saw; do not retry in a loop".

## Reviewer, gate or pair lane

- Head the `# Task` block with what is known: the implementer's report, CI's failure list with job names and run ids, and every finding still open from earlier rounds.
- For a later round of the same reviewer type, `read agent://<prior lane>/findings` and paste its ids with closed, partially open or open per id, so the rounds share one numbering.
- Before you write "closed" against an item the implementer marked done, `grep -n` its target symbol or docstring in the head.
- Record a claim the gate cannot reproduce as unreproduced on the surface it tried, not as wrong.
- Ask for what the reader saw outside the diff, flagged as unverified.
- Relay a round's findings once, after the last reader posts: a fix started on the first finding is often undone by the next. If you must send early, write "more is coming, do not start", and mark an early verdict provisional in its verdict line.

## Proof or multi-step verification lane

- List the steps, each beside the claim it proves, and ask for a verdict per step. A report covering fewer steps than the brief named is not a pass.
- Require "say nothing about a surface you did not read except that you did not read it".
- Use the verdicts of the `subagent-driven-development` implementer prompt and name all four, since a brief outside `sdd` does not inherit them: `DONE`, `DONE_WITH_CONCERNS` (done, with doubts), `BLOCKED` (cannot complete), `NEEDS_CONTEXT` (information missing).

## Survey or absence-claim lane

- Every survey brief says, in these words: "read from `origin/main` (`git show origin/main:<path>` or `gh api` contents at the ref), never the shared checkout's working tree; every absence claim names the command that would have found it, and a grep for a reference covers the rendered form too (`<a href>` as well as `<Link>`)". A shared checkout drifts, and a survey reports the drift as fact.
- Give a survey that needs `git` or `gh` to an agent that has `bash`, such as `task`.
- Run an absence search over the widest scope that costs no more time or credentials than the narrow one: a narrow scope does not just miss, it returns a confident wrong answer ("absent", "deleted").
- A "found nothing" from a search scoped to one system is evidence about that system only.
- Search the verb or artefact the thing writes, not only its noun, and every spelling: symbol (`ingest_eval_set`), dotted module (`ingest.eval_set`), file path (`ingest/eval_set.py`), rendered string. Rendered text can be wrapped in its source: search across newlines (`grep -Pzo`) or for fragments.
- A claim about what X does under Y reads Y's config as well as X's source; name the consumer's config file as a required read.
- A count is a lower bound unless the brief asks for the population, the scope and any retrieval limit beside it.

## Measurement lane

- Say what to do when the measurement is impossible, and name the fabrication you forbid (an invented number, a substitute mutant, a guessed value); the clause stops only what it names. A working form: "write the red test if an existing seam lets you inject the failure; if none exists, say so under Not covered; if a suggested mutant does not go red, report that as a finding rather than inventing a different mutant". In a plan, write the same line as a stop condition: when the measurement is impossible, the step stops and reports why.
- When the system refuses the measurement, name the value the lane reports in its place.
- "Cannot be measured" is a finding that needs its own control, paired with what can be measured ("unknowable, and here is who is affected").
- Count a content pattern in the corpus that produces the data, not in the sample you hold.
- Carry the denominator beside every count. Do not conclude while the population is empty, smaller than an independent count of the same scope (the source's own total or index), or still growing between two reads. Find which source produced a row labelled UNKNOWN before you treat it as a finding.
- Include in the case set the case your claim says cannot happen, and name it in the report.
- A positive control backs an absence claim only if it exercises the same path as the things claimed absent: one control per shape (auth mode, code path, input form).
- Where two implementations of one contract coexist, an end-to-end pass proves only the one it drove. Make parity a required check: name the other twin and ask for a decision-table comparison.
- A moving pin (`@main`) resolves at launch: read the resolved commit from the run's own packages.
- Trace a value to where it is written before anything depends on its name: open the file or function an import or reference points to, and do not infer its behaviour from its name. Read the field that defines membership (the full registry id, the owner table, the population key), not one that describes it.
- Before you take an item from another lane, check that its labels and component match your role: your role sets an instruction's scope.
- Guard an empty or missing value before the comparison it flows into, and make the guard say the input was missing.
