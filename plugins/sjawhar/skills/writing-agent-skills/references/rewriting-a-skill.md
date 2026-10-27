# Rewriting an existing skill

Run steps A, B and D on each skill you rewrite, and step C on the skills step C selects (the ten most-loaded, plus any whose rewrite drops or narrows a required step). Put each step's output in the commit message or the pull request, so the next editor can see why each line went. The rewriter never runs step A or step B itself, because an author does not see the gaps in their own text.

## Before you start

1. Copy the old text to a scratch directory (`cp -r <skill> "$(mktemp -d)"/old`). Step A and the behavior test compare against it.
2. Run the checks in step D on the old text and keep the output. The commit reports old and new numbers.
3. Find what the skill is for in practice: `tq search --commands 'skill://<name>' --days 30 --limit 20` lists sessions that loaded it. Read two or three at the point of load and list what each needed from the skill: a command, a fact, a correction the user gave. Write rules for those, not for what the old text says.

## Step A: map every instruction

The rewriter writes the new text and a drop list: each old instruction it removed, with one reason code and its evidence.

| Reason code | Evidence it needs |
|---|---|
| `duplicate` | Where the surviving copy is: file and section |
| `obsolete` | What changed: the model, the harness feature or the tool that now does it, with a link or a control run |
| `general` | A control run in which the model did the right thing with no guidance |
| `one-incident` | The `tq search` that finds no second session with that failure |
| `contradicted` | The file and line of code that does it differently |
| `moved` | The reference file or script it moved to |

Then a fresh agent that did not write the rewrite gets the old text, the new text and the drop list. It does not get the rewriter's reasoning. Brief it:

> List every instruction in OLD: each thing it tells the agent to do, not do, check or know. For each one, give the line in NEW that carries the same meaning and scope, or the drop-list row that covers it. Mark KEPT when NEW carries it, LOST when neither exists, NARROWED when NEW keeps the rule but for fewer cases or with a lost command, path or number, and BAD-DROP when the drop-list reason is not one of the six codes or its evidence does not show what it claims. Output a table: old line, instruction, new line or drop row, verdict.

Fix every LOST, NARROWED and BAD-DROP: restore the rule, or drop it with a real reason. Re-run the map only on the rows you changed. A rewrite that silently drops a rule is worse than no rewrite, and a narrowed rule fails where the old one held, because current models apply a rule only as broadly as it says.

## Step B: slop check

Another fresh agent reads only the new text and quotes each sentence that falls in one of these classes, with a concrete replacement. The examples below come from the skills this procedure rewrites.

| Class | Example from these skills | What the agent cannot do with it | Concrete form |
|---|---|---|---|
| Vague or empty phrase | "Be thorough and rigorous. Verify claims before reporting them." (thermonuclear-code-quality) | Act on it: no step, no object | "Before you report a finding, open the cited file:line and quote the line that supports it." |
| Abstract noun in place of an action | "Head moved unexpectedly: stop all mutation and settle ownership with whoever pushed." (landing-a-pr) | Know what to send to whom | "Message the session that pushed. Push nothing until it answers who owns the branch." |
| Undefined term | "`MERGEABLE`/`CLEAN`, zero backlog and a settled snapshot are necessary, not sufficient." (landing-a-pr); "Label turns Jev-first" (reflect) | Look up what "settled snapshot" or "Jev" means | Define the term at first use, or replace it with what it stands for |
| Rule nobody could check | "Predict [the user's] judgment, not his mood." (sami-proxy); "Neither your model nor theirs is the starting truth." (dispatch-brainstorming) | Show, in a transcript, that it obeyed | "Cite the earlier decision (`dispatch://KEY/ask/<id>`) your prediction rests on." |
| Clause that reopens the rule | "No turn budgets, step caps, or time boxes on a dispatch unless the task itself calls for one" (using-subagents); "This is a standing preference, not a rule to apply mechanically when it would delay something…" (proof-campaign-orchestration) | Tell when the exception applies, so it always can | State the condition: "Set a cap only when the user's request gives a number." |
| Incident story in place of a rule | "At 13:33Z on 2026-09-27, `agent(prompt, agent='task')` resolved to `openai/gpt-6-astra`…" (using-subagents) | Find the instruction inside the story | The rule plus its failure in one clause; the story goes in the commit message |
| Invented label | "Before you arm, stop every lane that can push" (landing-a-pr) | Map "arm" and "lane" to a command or an actor | "Before you run `gh pr merge --auto`, stop every session that can push to the branch." |

Also report: a rule with no reason next to it, a reason that only restates its rule, two rules that contradict, and a sentence a careful engineer would read twice.

Brief the slop agent with the table above and the new text only, and ask for: quote, class, replacement. The rewriter fixes each finding or answers it with one line of evidence. Fix a finding by replacing words where you can; for a missing reason or definition, add one clause and cut as many bytes elsewhere, because the rewrite may grow by 200 bytes at most (step D). Then a second round checks only the changed lines. Stop after three rounds and never report a finding twice: agent review loops do not end on their own, and a three-round cap, with no other change, took one-round passes from 65% to 78%.

## Step C: behavior test for the most-loaded skills

Run it for the ten most-loaded skills among those you are rewriting, by sessions in the last 7 days, and for any skill whose rewrite drops or narrows a required step. A lost rule does the most harm in a skill that many sessions load. The query below ranks all skills by sessions; keep the ones you are rewriting:

```bash
tq sql "SELECT extract(command, 'skill://([A-Za-z0-9_.-]+)') AS skill,
               uniqExact(session_id) AS sessions, count() AS loads
        FROM calls
        WHERE started_at >= now() - INTERVAL 7 DAY AND tool = 'read'
          AND position(command, 'skill://') > 0
        GROUP BY skill ORDER BY sessions DESC LIMIT 40"
```

This counts explicit `read skill://` calls only. A skill the harness injects at session start (such as `dispatch-first`) loads in every session and does not appear; treat it as most-loaded.

Step C compares old and new text only: it checks that the rewrite lost nothing, not whether each rule helps, so it needs no control arm.

1. Pick three cases from real sessions that loaded the skill, at least one where the agent got it wrong or the user corrected it (`tq search --human '<the skill's subject>'`). Write each as the raw task the session got, with no hint of the expected answer. An invented happy-path set scored 100% where real cases scored 26%.
2. For each case, write what a pass is before you run anything: the observable action or output, checkable from the transcript.
3. Run each case three times with the old text and three times with the new text, each in a fresh session that is not told it is a test. Interleave old and new runs (or batch them in random order), so a change in model or load hits both arms. Commands: `references/testing.md`.
4. Give each transcript, under a random ID, to a fresh scorer that does not know the arm, with the pass bar from step 2 (`references/testing.md`, Scoring).
5. Report `old k/n, new k/n` per case. The new text passes when, for every case, new k is at least old k minus 1. If a case fails, restore the dropped rule that the failing transcripts show was needed, and re-run that case only.

## Step D: size and description checks

```bash
python3 ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...
```

It prints, per skill: SKILL.md bytes and body words, description characters, rule lines, and each failed check. It exits 1 when any skill fails a hard check:

- SKILL.md over 20,000 bytes (Claude Code keeps about that much after compaction).
- Description over 1,024 characters (Codex's cap), empty, or not starting with "Use".
- A relative link or `references/`, `scripts/` path that does not exist in the skill directory.

It warns, without failing, at SKILL.md over 8,000 bytes (Codex keeps the first 8,000), description over 500 characters, and over 40 rule lines.

For a rewrite, add `--old <scratch>/old`. It then also fails when the new SKILL.md is more than 200 bytes larger than the old one, because a rewrite that grows defeats its purpose and files regrow on their own. If meeting the bar would drop a rule, keep the rule and give the byte count and the reason in the commit message.

Add `--log ~/.cache/skill-sizes.tsv` to append one dated row per skill. Run it daily for a week after the rewrite lands and report the growth, because rewritten instruction files regrew to 91.5% of their old size within 10 commits.
