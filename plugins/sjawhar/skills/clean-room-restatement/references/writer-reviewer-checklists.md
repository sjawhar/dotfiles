# The writer's refusals, the reviewer's verdicts, the auditor's rule

Three checklists. Copy each into the brief of the agent it binds; none of them replaces another.

## The writer's refused inputs

The writer works only from its package: the file with a marker in place of each removed item, the manifest row
per marker, the interface of each marked name, the list of what the file may use, and the build command. It
refuses these, and says plainly in its report if it took any of them:

- Any search for the removed text, anywhere: another copy of the file under another name, the version-control
  history (`show`, `log`, `diff`), a compiled artefact of the old file, an archive, another machine.
- Any copy of a proof, a body or a step order from a sibling written by the same hand as the old file. The
  library's own named lemmas and rules are not a copy; a sibling's step order is.
- Any change to a kept line, a name, a type or a statement the interface gives. An item that cannot be proved
  or implemented with its given statement is a stop-and-report, never a weakening.
- Any axiom, `admit`, `Admitted`, stub or hypothesis outside the statement.
- A generator is allowed where a file carries hundreds of near-identical lines, if its rows come from the
  pinned source of truth or the goal state and not from the old file. Put the generator in the package as
  evidence; hand-typing the rows is not the intent.

Anything the writer believes it needs from this list goes to its coordinator. The writer never decides it.

## The reviewer's verdicts

The reviewer sees both files and wrote neither; a reviewer that wrote any part of either refuses the task. It
gives every manifest row exactly one verdict, adds no row and leaves none out, and writes one sentence of
reason per row.

- **independent** — the new text differs from the old in structure, in the order of its steps, and in its local
  names.
- **statement-forced** — the new text is close to the old because the interface leaves no other form: a
  one-line definition, a premise whose type the interface gives byte for byte, a lemma whose proof is one named
  rule, or an item marked as duplicating a required file. The reason names the interface line, or the required
  file, that forces it.
- **follows-old** — the new text keeps the old text's step order, its local hypothesis names or its layout, and
  the interface does not force that.

**The evidence threshold: an undecidable row is `follows-old`.** Another write is cheap and a wrong pass is
not, and no later check recovers a pass that should not have been given.

The reviewer also checks, running the commands itself rather than reading the writer's report:

1. every kept line of the new file is byte-equal to the old file's kept line;
2. every marked name exists with the type the interface gives;
3. the closure check over each marked name names only allowed axioms, and nothing is admitted;
4. the new file holds no stub, no axiom and no admitted proof.

Its record is a table of name, kind, verdict and reason, then the four checks with their output, then one last
line giving the verdict, the commit and the item count — and the sentence saying what the pass shows and what
it does not.

## The auditor's rule

Audit every writer from its own session file, which keeps every tool result, before you accept its work: find
each line of a forbidden file that the writer printed, then those that declare one of that file's marked names.

- A writer that printed a **body** of one of its marked items is **void**. Throw the workspace away and start a
  fresh writer. Do not repair it and do not ask a reviewer to judge it, because a reviewer cannot tell
  afterwards.
- A writer that printed only a **header** — a name and its type, nothing right of the definition sign — is
  **not** void: that is the interface it already holds. Record that reasoning in the package, so the reviewer
  does not derive it again.

Both halves are needed: a grep for a declaration keyword prints headers, while a grep for a tactic name, or a
read of a line range, prints bodies. Run the audit over every writer, including those started after a
quarantine, because a quarantine is a mechanism and mechanisms fail.
