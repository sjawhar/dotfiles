# Rewriting an existing skill

Run steps A, B and D on each skill you rewrite, and step C on the skills it selects. Put each step's output in the commit message or the pull request, so the next editor sees why each line went. Fresh agents run steps A and B, never the rewriter: an author does not see the gaps in their own text.

## Before you start

1. Copy the old text to a scratch directory: `cp -r <skill> "$(mktemp -d)"/old`. Step A and step C compare against it.
2. Run step D on the old text and keep the output; the commit reports old and new numbers.
3. Find what the skill is for in practice: `tq search --commands 'skill://<name>' --days 30 --limit 20` lists sessions that loaded it. Read two or three at the point of load and list what each needed: a command, a fact, a correction the user gave. Write rules for those, not for what the old text says.

## Step A: map every instruction

The rewriter writes the new text and a drop list: each old instruction it removed, with one reason code and its evidence:

- `duplicate`: the file and section of the surviving copy.
- `obsolete`: the model, harness feature or tool that now does it, with a link or a control run.
- `general`: a control run in which the model did the right thing with no guidance.
- `one-incident`: the `tq search` that finds no second session with that failure.
- `contradicted`: the file and line of code that does it differently.
- `moved`: the reference file, docs page or script it moved to.
- `history`: a date, incident, measurement, issue key or decision record, which `updating-docs` keeps out of a doc.

Then a fresh agent gets the old text, the new text and the drop list, and not the rewriter's reasoning. Brief it:

> List every instruction in OLD: each thing it tells the agent to do, not do, check or know. For each one, give the line in NEW that carries the same meaning and scope, or the drop-list row that covers it. Mark KEPT when NEW carries it, LOST when neither exists, NARROWED when NEW keeps the rule for fewer cases or with a lost command, path or number, and BAD-DROP when the drop-list reason is not one of the codes or its evidence does not show what it claims. Output a table: old line, instruction, new line or drop row, verdict.

Fix every LOST, NARROWED and BAD-DROP: restore the rule, or drop it with a real reason. Re-run the map on the changed rows only. A narrowed rule fails where the old one held.

## Step B: slop check

Another fresh agent reads only the new text and quotes each sentence in one of these classes, with a concrete replacement:

- Vague or empty phrase ("Be thorough and rigorous"): the agent cannot act on it. Replace with a step and an object: "Before you report a finding, open the cited file:line and quote the line that supports it."
- Abstract noun in place of an action ("settle ownership"): the agent does not know what to send to whom. Replace with the message and its recipient.
- Undefined term ("a settled snapshot"): define it at first use, or write what it stands for.
- Rule nobody could check ("predict his judgment, not his mood"): replace with an action a transcript can show, such as citing the earlier decision the prediction rests on.
- Clause that reopens the rule ("unless the task calls for one"): state the condition, such as "Set a cap only when the request gives a number."
- Incident story in place of a rule: keep the rule and its failure in one clause; the story goes in the commit message.
- Invented label ("before you arm, stop every lane"): replace with the command and the actor ("before you run `gh pr merge --auto`, stop every session that can push to the branch").

Also report a rule with no reason, a reason that only restates its rule, two rules that contradict, and a sentence a careful engineer would read twice.

Brief the slop agent with the list above and the new text only; ask for quote, class and replacement. Fix each finding or answer it with one line of evidence. Fix a finding by replacing words where you can. For a missing reason or definition, add one clause and cut as many bytes of repeats and restated context elsewhere, because a rewrite may grow SKILL.md by 200 bytes at most (step D). A second round checks only the changed lines. Stop after three rounds, and never report a finding twice.

## Step C: behavior test for the most-loaded skills

Run it for the ten most-loaded skills among those you rewrite, by sessions in the last 7 days, and for any skill whose rewrite drops or narrows a required step. This query ranks skills by sessions:

```bash
tq sql "SELECT extract(command, 'skill://([A-Za-z0-9_.-]+)') AS skill,
               uniqExact(session_id) AS sessions, count() AS loads
        FROM calls
        WHERE started_at >= now() - INTERVAL 7 DAY AND tool = 'read'
          AND position(command, 'skill://') > 0
        GROUP BY skill ORDER BY sessions DESC LIMIT 40"
```

It counts explicit `read` calls only. A skill the harness injects at session start (such as `dispatch-first`) loads in every session: treat it as most-loaded.

Step C checks that the rewrite lost nothing, so it compares old and new text and needs no control arm.

1. Pick three cases from real sessions that loaded the skill, at least one where the agent got it wrong or the user corrected it (`tq search --human '<the skill's subject>'`). Write each as the raw task the session got, with no hint of the expected answer.
2. Write the pass bar for each case: an action or output a transcript shows.
3. Run each case three times with the old text and three times with the new, each in a fresh session that is not told it is a test. Interleave the arms so a change in model or load hits both. Commands: `testing.md`.
4. Give each transcript, under a random ID and with the pass bar from step 2, to a fresh scorer that does not know the arm (`testing.md`, "Scoring").
5. Report `old k/n, new k/n` per case. The new text passes when, for every case, new k is at least old k minus 1. If a case fails, restore the dropped rule the failing transcripts show was needed, and re-run that case only.

## Step D: size and description checks

```bash
python3 ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py [--old <scratch>/old] <skill dir>...
```

The limits are in SKILL.md, "Limits"; `--help` lists each check. For a rewrite, pass `--old` with the copy from "Before you start": the check then also fails when the new SKILL.md is more than 200 bytes larger than the old one, because a rewrite that grows defeats its purpose. A rule that would not fit moves with its whole block into a reference named at its step, never past the limits.

After the rewrite lands, run the check on each rewritten skill daily for a week, with `--log ~/.cache/skill-sizes.tsv` (one dated row per skill) and with `--old` on the SKILL.md that landed (`--help` shows how to pass it from `git show`), because rewritten instruction files regrow. Post the week's rows on the rewrite's pull request. When `--old` fails, the skill grew more than 200 bytes: shrink it as SKILL.md, "4. Remove rules", says.
