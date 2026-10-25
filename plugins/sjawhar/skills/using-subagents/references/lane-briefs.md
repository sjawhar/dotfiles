# Briefs by lane type

Everything in `SKILL.md` still applies. These are the extra lines each kind of lane needs.

Build a brief that carries code by concatenation or a quoted heredoc, never an f-string or template literal, because braces and backticks are what such a brief is full of; then `wc -c` the file and read its first and last lines before dispatching against it, because nothing errors on the dispatch side when the brief failed to write — the subagent simply has no instructions, which comes back looking like a bad agent.

## Who owns what

The `SKILL.md` rule for a subagent over a new peer session comes with a second half: work already in progress is finished before new work is started. A standing role Sami defined himself, such as an on-call lane that remediates incidents while the owner repairs the code, is his design and does not break "one deliverable, one owner".

## Tester or acceptance lane

- Brief it skeptical: the work is presumed broken until the tester proves otherwise on the real surface (the system the user runs, not a unit test), and unit-only verification does not qualify.
- On finding a defect the tester writes the failing red test itself. The resumed implementer's brief then says, in these words, "make the tester's red test pass; do not modify it", and the handoff marks tester-authored tests as such.
- Ask for a per-item verdict of RAN, BLOCKED or PARTIAL, never a bare pass or fail: a reader cannot tell a check that passed from one that never ran. PARTIAL means the check touched the path but asserted something adjacent, so it is not a pass. When the item is re-dispatched, carry each row's RAN or BLOCKED into the new brief, so a blocked row is not later read as run. Pair it with a stop condition, e.g. "if container creation hangs for more than 2 minutes, stop and report that item BLOCKED with the command and what you saw; do not retry in a loop".

## Reviewer, gate or pair lane

- Put what is already known at the head of the `# Task` block: the implementer's report file, CI's failure list with job names and run ids, and every finding still open from earlier rounds. Otherwise the gate spends its time rediscovering known state.
- When you dispatch the same reviewer type again for a later round (a fold): `read agent://<prior lane>/findings` and paste its ids into the brief with "closed / partially open / open" per id. A fresh quality lane without its predecessor's ids produces a second numbering nobody can reconcile against the first.
- Before writing "closed" against an item the implementer marked Done, `grep -n` that item's target symbol or docstring in the head. The report is a claim; the tree is the record.
- A claim the gate cannot reproduce is recorded as unreproduced on the surface it tried, not as wrong.
- Ask for what the reader saw outside the diff, flagged as unverified: the cheapest place to find the next defect is a reviewer already reading the neighbouring code.
- Relay a round's findings once, when the last reader has posted. A finding sent the moment it arrives starts a fix the next finding invalidates. If you must send early, write "more is coming, do not start" in that message, and mark an early verdict provisional in its verdict line, where a reader looking only at the verdict will see it.
- Brief the property the fix must have, and mark any implementation you name as a replaceable starting point.

## Proof or multi-step verification brief

- Enumerate the steps and demand a per-step verdict in the output contract, never "prove X": the brief's shape is what makes a partial report honest. A report covering fewer steps than the brief named is not a pass.
- Write each step beside the claim it proves, so a step that proves nothing the PR asserts is visible while the brief is being written. A reviewer cannot find what the brief did not ask.
- Require "say nothing about a surface you did not read except that you did not read it".
- Use the verdict vocabulary that already exists rather than inventing one — `DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT`, from the `subagent-driven-development` skill's implementer prompt, with DONE_WITH_CONCERNS for work completed under doubt, BLOCKED for cannot-complete and NEEDS_CONTEXT for missing information. Briefs written outside `sdd` do not inherit it, so name the four values.

## Survey or absence-claim lane

Every survey brief says, in these words: "read from `origin/main` (`git show origin/main:<path>` / `gh api` contents at the ref), never the shared long-lived checkout's working tree; every absence claim names the command that would have found it, and a grep for a reference covers the rendered form too (`<a href>` as well as `<Link>`)". A shared checkout drifts, and a survey run in it reports the drift as fact. The bundled `scout` agent has no `bash`, so run a survey that needs either command as a `task` agent instead.

- Run an absence search over the widest scope that costs no more time or credentials than the narrow one: a narrow scope does not merely miss, it returns a confident wrong classification ("absent", "deleted") of the shape you were screening for.
- A "found nothing" from a search scoped to one system is evidence about that system only.
- Search the verb or artefact the thing writes, not the noun you are asking about, and search every spelling: symbol (`ingest_hawk_eval_set`), dotted module (`ingest.hawk_eval_set`), file path (`ingest/hawk_eval_set.py`), rendered CLI string. A phrase visible in rendered output can be absent from every line of its source, so search across newlines (`grep -Pzo`) or for the fragments that survive wrapping.
- A claim about what X does under Y reads Y's config, not only X's source; name the consumer's config file as a required read.
- A Scout count is a lower bound unless the brief asks for the queried population, the scope and any retrieval limit beside it.

## Measurement lane

- Say what to do when the measurement is impossible, in the brief template: without it a lane fabricates a number or over-measures, and the two reports have the same shape. Name the fabrication you are forbidding (an invented number, a substitute mutant, a guessed value), because the clause only stops what it names. The working form: "write the red test if an existing seam lets you inject the failure; if none exists, say so under Not covered; if a suggested mutant does not go red, report that as a finding rather than inventing a different mutant". In a plan, write the same line as a stop condition: when the measurement is impossible, the step stops and reports why.
- When the system refuses the measurement, as opposed to it being impossible, the brief names the value the lane reports in its place.
- "Cannot be measured at all" is a finding, and it needs its own control showing the measurement really fails. Pair it with what can be measured, so the report says "unknowable, and here is exactly who is affected if it matters" rather than leaving a hole.
- When behaviour depends on a content pattern, count that pattern in the corpus that produces the data, not in the sample you hold, because a sample's count says nothing about how often the behaviour fires.
- Carry the denominator beside every count, and refuse to conclude while the population is implausibly small or still growing: an aggregate over an empty set returns the same number as an aggregate over a passing one. Before you treat a row labelled UNKNOWN as a finding, find which source produced it.
- A measurement's case set must contain the case your claim says cannot happen, or it can only agree with you. Choose cases from the claim's negation and say in the report which disconfirming case was included.
- A positive control backs an absence claim only if it exercises the same path as the things claimed absent: one control per shape (auth mode, code path, input form).
- Where two implementations of one contract coexist, an end-to-end pass proves only the one it drove. Make parity a required check: name the other twin and ask for a decision-table comparison.
- A moving pin hides the same hole: `@main` in an eval set resolves at launch, so read the resolved commit from the run's own packages rather than today's main.
- Trace a value to where it is written before anything depends on its name; open the file or function that an import or reference points to, and do not infer its behaviour from its name. Guard an empty or missing value before the comparison it flows into, and make the guard say the input was missing.
- A label is a claim about identity, not proof of it: read the field that defines membership — the live registry id (the full one, since a prefix is refused as "no live session"), the owner table, the population key — not the field that describes it. Do not infer meaning from a field's name; trace where it is written, because `isEnrolledIn2Sv` reads false for someone who holds passkeys and signs in fine. Before you take an item from another lane, check that its labels and component match your own role, because your role defines an instruction's scope.

## Relaying rules and premises

- Hand a lane a premise together with the instruction that would falsify it. A wrong instruction with a falsification clause holds the damage to one round; a plausible reason handed over without saying it is unverified does not.
- A standing rule you put in a brief is a copy you now own: retracting it means finding every brief that carries it, and a brief outlives the message that retracted its source. Check whom the source addressed before relaying it.
- Write each rule once, as its own short line, because two copies drift apart and a rule buried mid-paragraph is read past. Before you write a rule, grep for its subject; only a full read catches two copies that have already parted. Split a document only when its next reader (a handoff, a review, a dispatch that will quote it) needs it split, not when you notice the length. A log of changes starts with a summary of the current state; in a list of rules, start each item with its subject.
- Point at the skill rather than paraphrasing it, and head any restatement "the skill is the authority; where they differ, the skill wins". A paraphrase drifts when it is written, not later: it keeps what you were thinking about and drops what you were not, and both texts read as true until a live case needs the missing clause. Audit a relayed block against the skill line by line and report the clean lines too, so the audit shows its coverage. Keep such a block in a file, not in a kernel cell that gets rebuilt from memory.
- Restating a claim more strongly than its source makes the stronger claim yours. If nobody measured the strengthening, mark it unmeasured or do not send it.
- Re-derive a claim before you act on it: echoing one turns someone else's uncertainty into your own assertion.
- Re-derive a scout's test matrix from the scout's own predicate before handing it to a builder: a build agent takes the table as given and pins the wrong behaviour, e.g. a test that refuses input the code correctly allows. The scout surfaces a contradiction; the dispatcher resolves it in the design report, before any test exists.
- "On main" in a brief is a branch name, and the worker measures whatever tree its workspace actually holds.
- Before naming a contract field's type in a brief, run the constructor: a type written from memory of an API is a hypothesis, and the one-liner is the cheapest test of it.
- A brief about Inspect, the `inspect_*` forks, Hawk, middleman or the bridge names the code paths the worker must read first — the fork checkout or the installed package under the venv — and requires a `file:line` citation for every claim about how those systems behave; an uncited claim is written as an assumption. Where a repo keeps a known-misreads list (`docs/architecture/facts.md`), name it as the first read.
