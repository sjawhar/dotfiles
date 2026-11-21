# Rewriting an existing skill

Rewrite or shorten a skill so that it keeps each rule an agent needs and loses the rest.

**Done when:** steps A, B and D pass on each skill you rewrite, step C passes on the skills it selects, and each step's output is in the commit message or the pull request.

Run steps A, B and D on each skill you rewrite, and step C on the skills it selects. Put each step's output in the commit message or the pull request, so the next editor sees why each line went. Fresh agents run steps A and B, never the rewriter: an author does not see the gaps in their own text.

## Before you start

1. **Copy the old text** to a scratch directory: `cp -r <skill> "$(mktemp -d)"/old`. Step A and step C compare against it.
2. **Measure the old text.** Run step D on it and keep the output; the commit reports old and new numbers.
3. **Find what the skill is for in practice.** `tq search --commands 'skill://<name>' --days 30 --limit 20` lists sessions that loaded it. Read two or three at the point of load and list what each needed: a command, a fact, a correction the user gave. Write rules for those, not for what the old text says.

## Step A: Map every instruction

The rewriter writes the new text and a drop list: each old instruction it removed, with one reason code and its evidence.

| Code | Evidence |
| --- | --- |
| `duplicate` | The file and section of the surviving copy |
| `obsolete` | The model, harness feature or tool that now does it, with a link or a control run |
| `general` | A control run in which the model did the right thing with no guidance |
| `one-incident` | The `tq search` that finds no second session with that failure |
| `contradicted` | The file and line of code that does it differently |
| `moved` | The reference file, docs page or script it moved to |
| `history` | A date, incident, measurement, issue key or decision record, which `updating-docs` keeps out of a doc |

Then a fresh agent gets the old text, the new text and the drop list, and not the rewriter's reasoning. Brief it:

> List every instruction in OLD: each thing it tells the agent to do, not do, check or know. For each one, give the line in NEW that carries the same meaning and scope, or the drop-list row that covers it. Mark KEPT when NEW carries it, LOST when neither exists, NARROWED when NEW keeps the rule for fewer cases or with a lost command, path or number, and BAD-DROP when the drop-list reason is not one of the codes or its evidence does not show what it claims. Output a table: old line, instruction, new line or drop row, verdict.

Fix every LOST, NARROWED and BAD-DROP: restore the rule, or drop it with a real reason. Re-run the map on the changed rows only. A narrowed rule fails where the old one held.

## Step B: Check for slop

Another fresh agent reads only the new text and quotes each sentence in one of these classes, with a concrete replacement:

| Class | Example | Replace it with |
| --- | --- | --- |
| Vague or empty phrase: the agent cannot act on it | "Be thorough and rigorous" | A step and an object: "Before you report a finding, open the cited file:line and quote the line that supports it." |
| Abstract noun in place of an action: the agent does not know what to send to whom | "settle ownership" | The message and its recipient |
| Undefined term | "a settled snapshot" | Its definition at first use, or what it stands for |
| Rule nobody could check | "predict his judgment, not his mood" | An action a transcript can show, such as citing the earlier decision the prediction rests on |
| Clause that reopens the rule | "unless the task calls for one" | The condition: "Set a cap only when the request gives a number." |
| Incident story in place of a rule | A dated account of what went wrong | The rule and its failure in one clause; the story goes in the commit message |
| Invented label | "before you arm, stop every lane" | The command and the actor: "before you run `gh pr merge --auto`, stop every session that can push to the branch" |

Also report a reason that only restates its rule, two rules that contradict, a sentence a careful engineer would read twice, and a line that breaks a rule of `style.md`.

1. **Brief the slop agent** with the table above and the new text only; ask for quote, class and replacement.
2. **Answer each finding.** Fix it, or answer it with one line of evidence. Fix a finding by replacing words where you can. For a missing definition, add one clause.
3. **Re-check only the changed lines** in a second round. Stop after three rounds, and never report a finding twice.

## Step C: Test the behavior of the most-loaded skills

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

1. **Pick three cases** from real sessions that loaded the skill, at least one where the agent got it wrong or the user corrected it (`tq search --human '<the skill's subject>'`). Write each as the raw task the session got, with no hint of the expected answer.
2. **Write the pass bar** for each case: an action or output a transcript shows.
3. **Run both arms.** Run each case three times with the old text and three times with the new, each in a fresh session that is not told it is a test. Interleave the arms so a change in model or load hits both. Commands: `testing.md`.
4. **Score blind.** Give each transcript, under a random ID and with the pass bar from step 2, to a fresh scorer that does not know the arm (`testing.md`, "Scoring").
5. **Report `old k/n, new k/n` per case.** The new text passes when, for every case, new k is at least old k minus 1. If a case fails, restore the dropped rule the failing transcripts show was needed, and re-run that case only.

## Step D: Check the size and the description

```bash
uv run --script ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...
```

The limits are in SKILL.md, "Limits"; `--help` lists each check. A rule that would not fit moves with its whole block into a reference named at its step, never past the limits.

After the rewrite lands, run the check on each rewritten skill daily for a week, with `--log ~/.cache/skill-sizes.tsv` (one dated row per skill), because rewritten instruction files regrow. Post the week's rows on the rewrite's pull request. When a skill passes a limit, shrink it as SKILL.md, "Step 4: Remove rules", says.
