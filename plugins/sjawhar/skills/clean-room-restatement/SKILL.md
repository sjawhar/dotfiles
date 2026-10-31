---
name: clean-room-restatement
description: "Use when text must be written again so that the result is its author's own and not a derivative of an existing file: a clean-room rewrite, a re-implementation under a provenance or licence constraint, an independence or no-contamination requirement over code, proofs or prose. Also when you design the isolation, the interface or the review for one, when you judge whether a finished one is independent, and when the rewrite looks small enough to do by hand."
---

# Clean-room restatement

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

The method has five roles and they are five different agents. A tool that may see the old file replaces every
top-level item holding flagged text with a marker and keeps every other line byte-equal. It prints each marked
item's interface from a compiled unit, and a sanitiser strips what would leak. A writer that has never seen the
old file states each item again. A reviewer that sees both files, and wrote neither, gives each item one
verdict. Then the unit is rebuilt and its acceptance check of record is run.

## 1. Size the work before you promise it

- Restate whole items, not flagged lines: a flagged line sits inside a definition or proof that must be written
  again as a whole. Measured over one corpus, 87,308 flagged lines in 1,523 files sat inside items holding
  622,837 lines, 63% of the text, so block-level restatement is right but it is not a cheap patch.
- Where several units hold the same text once a name is substituted, have one writer state it once and let each
  other unit take that text, each with its own review, its own build and its own check. 61 of 277 files in one
  programme were such members, in 11 families; writing each again buys nothing and costs a writer.
- Plan around the build, not the writing. Measured per unit: extraction 11 to 164 s, interface printing 0.4 to
  6.4 min per file (median 1.8), and a baseline build of 1,396 to 8,541 compiled files. The build is the
  bottleneck, so stage build machines and a shared content-keyed cache before you stage writers.

## 2. Cut the leak paths before a writer starts

- Quarantine the writer's own worktree, not only the file. A worktree reaches the old text through sibling
  files: near-copies of the restated file under another name (one unit held a near-copy of each restated file,
  differing in 154 to 1113 lines; another's copies differed by 37% to 78%), and files that define a single
  marked name. One writer printed a definition body through a plain grep of a sibling and had to be thrown away.
- Quarantine by text, not by name, and apply the test to the ITEM as well as to the file. A marked name alone
  over-quarantines and closes nothing: one unit had a marked name that named a different, unrelated item in
  480 of its files. Quarantine a sibling whose definition of a marked name is byte-equal after normalising, or
  which shares at least 20% of a marked item's lines, or at least 30% of the file's lines. The item test is
  what catches the dangerous case: one voided writer's sibling held 19% of the file's lines and 64% of the
  lines of the item being restated.
- Look for the same item under a DIFFERENT name. The siblings that leaked were not the same-name files: they
  declared the same items with a changed suffix or a changed prefix. A quarantine built from names lets every
  one of them through.
- A CONSUMER of an item is not a sibling: it shares the text because it restates the statement in its own
  lemmas, and that contract is what the writer needs. Quarantine a heavy consumer and hand the writer a
  generated list instead, holding every line of every consumer that mentions one of its names, with file, line
  and a little context, and tell it that context may carry proof text it must not follow.
- A writer may not keep its own copy of the sources. One writer had archived them into its workspace before
  the quarantine existed, where no quarantine on the shared machine reaches.
- Move the compiled artefact with the source. In Rocq, `Print` of a transparent constant prints its body and
  `Print` of an opaque proof read from a `.vo` prints the old proof term; every compiled or cached form of a
  quarantined file leaks the same way.
- Never stub, weaken or admit a file the writer must require, even in the writer's own tree: that changes the
  environment the work is developed against, and the defect appears only at the central build. Mark the item as
  duplicating a required file, let the writer use that file as the library it is, and let the reviewer call the
  item statement-forced, naming the file.
- Write a unit in dependency order, and start a wave when its upstream COMPILES rather than when its reviewer
  passes: a send-back changes a proof, and a statement is byte-equal by construction, so downstream work
  survives it. The exception is a send-back on a definition, notation, record or inductive, which changes a
  body and so every downstream digest; review a wave's definition-like items before its lemmas, so the
  expensive send-back is found first. One unit held 168 definitions among 515 items.
- A quarantined worktree can no longer build the whole unit, and that is expected. The writer builds its own
  file and the consumers of its items; the whole-unit build and the acceptance check run centrally.

## 3. Hand over an interface that carries no body

- Check what the interface printer prints before you trust it: the interface you hand a writer can itself carry
  the text you are removing. In Rocq this is concrete — `About` prints the body of a `Notation` or an
  `Abbreviation` — so a restated `Notation`, `Abbreviation`, `Ltac` or `Ltac2` gets its name, its scope and its
  arity only, plus the type of the term a notation stands for. Clean any package already built from a printer
  you have not checked.
- Make an empty interface a loud failure: the tool exits non-zero and names the item. An empty interface is
  silent, because the writer has nothing to work from and produces something plausible anyway; one tool defect
  would have emptied every interface in a run and was caught only by a worker who read one.
- Give the writer its refused-input list verbatim from
  [the checklists](references/writer-reviewer-checklists.md), and route anything it believes it needs from that
  list to its coordinator. A writer that decides its own exception cannot be cleared afterwards.

## 4. Audit each writer from its session, then review the text

- Audit every writer from its own session file, which keeps every tool result, rather than from its report: a
  printed body voids the writer, a printed header does not. One audit of nine writers voided one and cleared
  eight on that rule. The audit does not replace the quarantine and the quarantine does not replace the audit.
- Give every item exactly one of three verdicts — independent, statement-forced, follows-old — with one
  sentence of reason, and send back anything you cannot decide. Verdicts, their evidence threshold and the
  reviewer's four mechanical checks are in [the checklists](references/writer-reviewer-checklists.md).
- Say what the pass shows: a reader who saw both files found no item that follows the old one. It does not show
  what the writer knew, and it does not show that the new text is the only text a writer could have produced.
  Write that sentence into the record, so nobody later reads the pass as a statement about the writer.
- Finish each unit centrally: rebuild it and run its acceptance check of record. A writer's narrow build
  checked its own file, not the unit.

## 5. One hand owns the shared tools

Name one maintainer for the extraction and checking tools and send it your case, your patch and your regression
result; never edit the shared copy yourself. Six workers found defects in two shared tools within half an hour,
each in a private copy, and private copies fork the method. Use your patched copy meanwhile, then run the check
again on the shipped tool so your result rests on what ships.

## Related

- [`review-judgment`](../review-judgment/SKILL.md): deciding which reviewer findings to act on when several
  passes disagree.
- [`using-subagents`](../using-subagents/SKILL.md): briefing and coordinating the writers, reviewers and tool
  owner as separate agents.
