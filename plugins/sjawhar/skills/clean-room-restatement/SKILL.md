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

Restate whole ITEMS, not flagged lines: a flagged line sits inside a definition or proof that must be written
again as a whole, so the work is far larger than the flagged count suggests. Where several units hold the same
text once a name is substituted, one writer states it once and each other unit takes that text with its own
review, build and check; prove membership byte for byte first, because near-identical is not identical. Plan
around the BUILD rather than the writing: it is the step more hands do not speed up. Before you promise a date
for a programme of many units, read [sizing a programme](references/sizing-a-programme.md), which carries the
measured costs, the rework to expect, and how to order the work and schedule dependency waves.

## 2. Cut the leak paths before a writer starts

- Quarantine the writer's own worktree, not only the file. A worktree reaches the old text through sibling
  files: near-copies of the restated file under another name (one unit held a near-copy of each restated file,
  differing in 154 to 1113 lines; another's copies differed by 37% to 78%), and files that define a single
  marked name. One writer printed a definition body through a plain grep of a sibling and had to be thrown away.
- Quarantine by TEXT, never by name, and test the ITEM as well as the file. A marked name alone
  over-quarantines and closes nothing: one unit had a marked name that named an unrelated item in 480 of its
  files. Quarantine a sibling when any of these holds: its definition of a marked name is byte-equal after
  normalising; it shares 20% of a marked item's lines; it shares 10 distinctive lines of any ONE item,
  whatever the share; it shares 30% of the file's lines. The last two catch what the first miss: one voided
  writer's sibling held 19% of the file and 64% of the item, and another held 12% of a 251-line item, which
  is 29 lines of real proof.
- Normalise the variant tag, not only digits, and look for the same item under a DIFFERENT name. The siblings
  that leaked were not the same-name files: they declared the same items with a changed suffix or prefix. One
  family read 15% shared on digits alone and 73% to 94% once its tag was normalised.
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
- Write a unit in dependency order; [sizing a programme](references/sizing-a-programme.md) has the wave rule
  and its one exception.
- A quarantined worktree can no longer build the whole unit, and that is expected. The writer builds its own
  file and the consumers of its items; the whole-unit build and the acceptance check run centrally.

## 3. A clean writer can still be sent back, and the control is the brief

Put this before the leak rules in a reader's mind, because a reader who meets only those will not see it
coming. The first review of one programme sent back 18 of 42 items from a writer whose audit was clean: its
proofs were short, and it used the hypothesis names a model reaches for first, in the old text's order. A
reviewer cannot tell that apart from copying, so it sends it back and the file is written twice.

That is a different failure from a leak and it needs a different control: the writer's brief, not the
quarantine. Tell a writer, before it starts, to name hypotheses for what they say in a scheme of its own, to
reason forward rather than back from the goal in the order the statement lists its premises, to choose its own
case order and split points, and not to take the first spelling of a tactic that comes to mind. None of that
is about making text differ for its own sake: an item whose only possible form is the old one is fine and the
reviewer marks it statement-forced. It is about the items where the writer had a choice and took the default
without noticing.

Shared hypothesis names are the signal this failure leaves, and a reviewer reading for style will not count
them by hand. Where a comparison aid exists that prints the longest shared tactic run and the shared
hypothesis names beside the identical-line count, name it in the reviewer's step; let it inform the reviewer
and decide nothing.

## 4. Hand over an interface that carries no body

- READ a package before you hand it over, for every kind of item it restates. The interface you hand a writer
  can carry the very text you are removing, by more routes than you will guess: one programme found three in
  a day, each through a different printer, each in packages already built, and two of them in the patch its
  own workers were sharing. A restated item whose body IS the text being written gets its name, its scope and
  its arity only.
- Make an empty interface a loud failure: the tool exits non-zero and names the item. An empty interface is
  silent, because the writer has nothing to work from and produces something plausible anyway; one tool defect
  would have emptied every interface in a run and was caught only by a worker who read one.
- Give the writer its refused-input list verbatim from
  [the checklists](references/writer-reviewer-checklists.md), and route anything it believes it needs from that
  list to its coordinator. A writer that decides its own exception cannot be cleared afterwards.

## 5. Audit each writer from its session, then review the text

- Audit every writer from its own session file, which keeps every tool result, rather than from its report.
  Search that file for the DISTINCTIVE LINES of the removed items, not for header-shaped lines: a header rule
  cleared a writer that had printed 29 of the 251 proof lines of its own item through one `sed` over a
  sibling, and two of those lines stood in what it committed. A printed body voids the writer; a printed
  header, a name and its type, does not. The audit does not replace the quarantine and the quarantine does
  not replace the audit.
- Never send a late instruction to a running writer. One coordinator messaged its four running writers two
  minutes after dispatch and the harness killed all four mid-turn. Put a late rule in the workspace files and
  in the next dispatch, or apply it on a send-back.
- Give every item exactly one of three verdicts — independent, statement-forced, follows-old — with one
  sentence of reason, and send back anything you cannot decide. The reviewer's mechanical checks are
  line-oriented and type-oriented, and those are exactly the checks a verbatim copy in the same order with
  the same binder names passes, so they cannot carry the verdict alone: where an aid exists that counts the
  longest shared tactic run and the shared binder names, name its output in the reason. Verdicts, their
  evidence threshold and the mechanical checks are in [the checklists](references/writer-reviewer-checklists.md).
- Say what the pass shows: a reader who saw both files found no item that follows the old one. It does not show
  what the writer knew, and it does not show that the new text is the only text a writer could have produced.
  Write that sentence into the record, so nobody later reads the pass as a statement about the writer.
- Finish each unit centrally: rebuild it and run its acceptance check of record. A writer's narrow build
  checked its own file, not the unit.

## 6. One hand owns the shared tools

Name one maintainer for the extraction and checking tools and send it your case, your patch and your regression
result; never edit the shared copy yourself. Six workers found defects in two shared tools within half an hour,
each in a private copy, and private copies fork the method. Use your patched copy meanwhile, then run the check
again on the shipped tool so your result rests on what ships.

## Related

- [`subagent-dispatch-fidelity`](../subagent-dispatch-fidelity/SKILL.md): briefing the writers, reviewers and
  tool owner as separate agents, and keeping each brief what you meant to send.
