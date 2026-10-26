---
name: writing-agent-skills
description: "Use when you create, edit, rewrite, shorten, test or delete an agent skill (a SKILL.md, its references or its description), or when you decide whether a correction or lesson should become a rule in a skill or an agent instruction file. Also when a skill seems too long, ignored or triggered wrongly. Not for text that a person reads."
---

# Writing agent skills

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## 1. Decide that a rule is needed

- Add a rule only for a failure you saw without it: run the task in a fresh session with no guidance, and see it fail in at least 2 of 3 runs. Otherwise the rule costs tokens on every load and crowds out rules that matter.
- Count the failure before you write for it: find it in at least two sessions with `tq search` (`skill://using-transcript-store`), not one. One incident turned into one rule is how instruction files triple in size.
- Fix the tool, or add a script or a hook, before you write prose. A check that a program runs holds where prose does not (88% against 67% compliance).
- Extend a skill that loads for the task before you create one. Four or more skills in one task give about half the gain of one to three.

## 2. Write the description

- Say only when to use the skill: start with "Use" ("Use when", "Use for", "Use before"), then the situations, symptoms and words a task would contain. Never summarize the steps, because agents then follow the summary and skip the body.
- Keep it under 500 characters (hard cap 1,024). Harnesses drop the descriptions of rarely used skills first when the listing runs over budget.
- Name the case where the agent thinks it can do without the skill ("also when the diff is pasted inline"). One such clause took a review skill from 7 of 27 loads to 21 of 27.
- If two skills still load on the same prompts after one description rewrite, merge them or split their scope; more words will not separate them.

## 3. Write the body

- Keep SKILL.md under 8,000 bytes (all Codex reads) and never over 20,000 (about what Claude Code keeps after compaction). Put required steps and stop rules first, because both cuts keep only the start.
- Move each block that only some tasks need to `references/<topic>.md`, and name it at the step that needs it, with the condition to read it. Exhaustive skills added almost nothing to task success.
- Over 40 rule lines (every bullet or numbered line; `check_skill.py` counts them), split the skill or delete rules. Near 80 rules, no model obeyed them all.
- Put every rule's reason next to it: the failure it prevents, in one clause. Inline reasons cut excess rules from +211% to +1.4%. A reason that restates the rule does nothing.
- Choose the form by the failure, because a form that does not match it adds text without fixing it:
  - The agent lacks a fact (a path, a command, a trap): state the fact.
  - The output has the wrong shape: give its parts in order, or a template. Prohibition lists produced more unwanted output than no guidance.
  - The agent knows the rule and skips it under an excuse ("the change is small"): state the rule, the excuse and the answer to it.
  - A program can check it: write the check and name the command.
  - The agent already does it in a control run: write nothing.
- Write plain English: short sentences, one idea each, the words the code already uses, a verb and an object in every instruction. Not ASD-STE100: a strict rewrite lost 46.8% of the facts, plain simple English 8.5%.
- State the condition that decides; never add "unless it matters" or "where reasonable". One such clause turned a consistent instruction into a noisy one.
- Keep incident stories, dates and URLs out of the body; put them in the commit message, because the agent reads a story as an instruction and misses its rule.
- Check that every path, command and skill you name exists. An agent that follows a dead pointer creates the missing thing.
- Copy the precedence sentence above word for word into every skill. Agents followed a stale file over the code 90% of the time without it, 9% with it.

## 4. Test it

Test every new skill and every edit that changes what an agent does: untested agent-written skills scored below no skill at all. Commands and scoring: `references/testing.md`.

- Run each test in a fresh session that gets the raw task, not your diagnosis, and does not know it is a test. Context left over from writing the skill hides its gaps.
- Compare the old text with the new text, plus a control with no guidance (each is an arm). Without the control you cannot tell a rule from what the model does anyway.
- Run 3 times per arm, 3 more if the arms differ by one or two runs; write the pass bar first; report `k/n` per arm. Twelve runs of one unchanged skill passed only 7 times.
- Take cases from real sessions where the failure happened. An invented set scored 100% where real cases scored 26%.
- For a description, also run near-miss prompts that must not load it. Otherwise you miss a description that loads on the wrong tasks.
- Read each transcript and quote the line that decided its score; a run can pass by luck or fail for an unrelated reason.
- Tune on some cases and report on others; revert a change that improves only the tuned ones. A self-tuned instruction file won 5 training tasks and lost 10 held-out ones.
- Stop a review loop after three rounds, check only what changed, and never repeat a finding. Agent-reviews-agent loops do not end by themselves.

## 5. Remove rules

- To fix a rule agents disobey, delete it and run the same task 3 times with the rest of the skill unchanged; rewrite it only if the failure shows in 2 of the 3. Repeated rewrites made one lesson set worse than none.
- Delete a rule when its reason no longer holds: a duplicate, written for an older model, obeyed unprompted in a control run, one incident that never recurred, or now done differently by the code or the harness.
- For a stale-skill review, run `scripts/stale_skills.py`. STALE alone is not a reason: decide each by the reasons above. Delete, never archive: git log is the archive.
- "It never fires" is not a reason by itself: run the control, because deleting ignored rules can remove helpful ones.
- For each required step you remove, name what now makes it happen (a script, a hook, another rule). If nothing does, keep the step, because a step left to choice gets skipped.
- Keep the words that set a rule's scope ("before every push", "in Rust files"). Current models apply a rule only as broadly as it says.
- Shrink by moving or deleting whole blocks, never by squeezing wording, which drops rules unnoticed.
- A rewrite may make SKILL.md at most 200 bytes longer, room for the precedence sentence (`check_skill.py --old` enforces it). Pay for each added reason or definition by cutting repeats and restated context, because files regrow on their own.

## 6. Rewrite an existing skill

Follow `references/rewriting-a-skill.md`: an instruction map and a slop check by fresh agents, a behavior test for the most-loaded skills, then the checks: `python3 ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...`
