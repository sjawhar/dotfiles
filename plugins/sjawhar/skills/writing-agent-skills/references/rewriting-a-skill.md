# Rewriting an existing skill

Rewrite or shorten a skill so that it keeps each rule an agent needs and loses the rest.

**Done when:** steps 1, 2 and 4 pass on each skill you rewrite, step 3 passes on the skills it selects, each step's output is in the commit message or the pull request, and the week of checks after it lands is posted.

Fresh agents run steps 1 and 2, never you: an author does not see the gaps in their own text. Put each step's output in the commit message or the pull request, so that the next editor sees why each line went.

## Before you start

1. **Copy the old text** to a scratch directory: `cp -r <skill> "$(mktemp -d)"/old`. Steps 1 and 3 compare against it.
2. **Measure the old text** with step 4, and keep the output for the commit, which reports old and new numbers.
3. **Find what the skill is for in practice.** `tq search --commands 'skill://<name>' --days 30 --limit 20` lists sessions that loaded it. Read two or three at the point of load, and list what each needed: a command, a fact, a correction the user gave.
4. **Write rules for those needs**, not for what the old text says.

## Step 1: Map every instruction

1. **Write the new text and a drop list:** each old instruction you removed, with one reason code and its evidence.

   | Code | Evidence |
   | --- | --- |
   | `duplicate` | The file and section of the surviving copy |
   | `obsolete` | The model, harness feature or tool that now does it, with a link or a control run |
   | `general` | A control run in which the model did the right thing with no guidance |
   | `one-incident` | The `tq search` that finds no second session with that failure |
   | `contradicted` | The file and line of code that does it differently |
   | `moved` | The reference file, docs page or script it moved to |
   | `history` | A date, incident, measurement, issue key or decision record, which `updating-docs` keeps out of a doc |

2. **Brief a fresh agent** with the old text, the new text and the drop list, and not your reasoning:

   > List every instruction in OLD: each thing it tells the agent to do, not do, check or know. For each one, give the line in NEW that carries the same meaning and scope, or the drop-list row that covers it, and mark it with a verdict: KEPT (NEW carries it), LOST (neither exists), NARROWED (NEW keeps the rule for fewer cases, or with a lost command, path or number), or BAD-DROP (the drop-list reason is not one of the codes, or its evidence does not show what it claims). Output a table: old line, instruction, new line or drop row, verdict.

3. **Fix every LOST, NARROWED and BAD-DROP:** restore the rule, or drop it with a real reason. A narrowed rule fails where the old one held.
4. **Map the changed rows again**, and only those.

## Step 2: Check for slop

1. **Brief another fresh agent** with the new text, `style.md` and the table below, and nothing else. Ask it to quote each sentence of a class, with the class and a concrete replacement.

   | Class | Example | Replace it with |
   | --- | --- | --- |
   | Vague or empty phrase: the agent cannot act on it | "Be thorough and rigorous" | A step and an object: "Before you report a finding, open the cited file:line and quote the line that supports it." |
   | Abstract noun in place of an action: the agent does not know what to send to whom | "settle ownership" | The message and its recipient |
   | Undefined term | "a settled snapshot" | Its definition at first use, or what it stands for |
   | Rule nobody could check | "predict his judgment, not his mood" | An action a transcript can show, such as citing the earlier decision the prediction rests on |
   | Clause that reopens the rule | "unless the task calls for one" | The condition: "Set a cap only when the request gives a number." |
   | Incident story in place of a rule | A dated account of what went wrong | The rule and its failure in one clause; the story goes in the commit message |
   | Invented label | "before you arm, stop every lane" | The command and the actor: "before you run `gh pr merge --auto`, stop every session that can push to the branch" |
   | A reason that only restates its rule | "Test it, because it needs testing" | The failure the rule prevents, or no reason |
   | Two rules that contradict | Two lines that order opposite actions in one case | One rule, or the condition that separates the two |
   | A sentence a careful engineer would read twice | Three rules packed into one sentence | Short sentences, one idea each |
   | A line that breaks a rule of `style.md` | A rule buried in a paragraph | The line in that rule's form |

2. **Answer each finding:** fix it, or answer it with one line of evidence. Fix a finding by replacing words, and add a clause only for a missing definition.
3. **Check only the changed lines** in each later round.
4. **Stop after three rounds**, and report no finding twice.

## Step 3: Test the behavior of the most-loaded skills

Run this step for the ten most-loaded skills among those you rewrite, by sessions in the last 7 days, and for each skill whose rewrite drops or narrows a required step. This query ranks skills by sessions:

```bash
tq sql "SELECT extract(command, 'skill://([A-Za-z0-9_.-]+)') AS skill,
               uniqExact(session_id) AS sessions, count() AS loads
        FROM calls
        WHERE started_at >= now() - INTERVAL 7 DAY AND tool = 'read'
          AND position(command, 'skill://') > 0
        GROUP BY skill ORDER BY sessions DESC LIMIT 40"
```

It counts explicit `read` calls only. Treat a skill that the harness injects at session start (such as `dispatch-first`) as most-loaded: it loads in every session. Run no control arm: this step compares old and new text, to show that the rewrite lost nothing.

1. **Pick three cases** from real sessions that loaded the skill, at least one where the agent got it wrong or the user corrected it (`tq search --human '<the skill's subject>'`). Write each as the raw task the session got, with no hint of the expected answer.
2. **Write the pass bar** for each case: an action or output a transcript shows.
3. **Run both arms.** Run each case three times with the old text and three times with the new, each in a fresh session that is not told it is a test, as `testing.md`'s "Running a body test" says.
4. **Score blind.** Give each transcript, under a random ID and with the pass bar from step 2, to a fresh scorer that does not know the arm (`testing.md`, "Scoring").
5. **Report `old k/n, new k/n` per case.** The new text passes when, for every case, new k is at least old k minus 1.
6. **Restore what a failing case needed.** If a case fails, restore the dropped rule that its failing transcripts show was needed, and run that case again, alone.

## Step 4: Check the size and the description

1. **Run `check_skill.py`** on each skill, as SKILL.md's "Limits" says.
2. **Move a rule that would not fit** into a reference, with its whole block, and name the reference at its step; never pass the limits.
3. **Watch the week after it lands.** Run the check on each rewritten skill daily for a week, with `--log ~/.cache/skill-sizes.tsv` (one dated row per skill): rewritten instruction files regrow.
4. **Post the week's rows** on the rewrite's pull request.
5. **Shrink a skill that passes a limit** as SKILL.md, "Step 4: Remove rules", says.
