---
name: writing-agent-skills
description: "Use when you create, edit, shorten, test or delete an agent skill (its SKILL.md, references or description), or decide whether a lesson should become a rule in a skill. Also when a skill seems too long, is ignored, or loads on the wrong tasks. Not for text a person reads."
---

# Writing agent skills

An agent guide (AGENTS.md, CLAUDE.md) follows `updating-docs` instead. Every line of a skill also passes that skill's "The test for every line": no history, no restated code, no emphasis as force.

## Limits

- `description`: starts "Use when", at most 300 characters, says when to load the skill and never its steps: an agent that reads the steps there follows them and skips the body.
- Put the trigger words first, with the case where the agent thinks it can do without the skill ("also when the diff is pasted inline"). omp shows the model a hint of about 12 words made from the description, or its first 99 characters until that hint exists.
- SKILL.md body: at most 150 lines and 8 KB (8,192 bytes). It holds the routing and the rules the model would not follow by default. Required steps and stop rules go first, because a cut keeps only the start.
- References: in `references/`, one level deep, each at most 150 lines and 8 KB. Name each one in SKILL.md at the step that needs it, with the condition: "Read `references/<topic>.md` when ...". Merge a small reference into SKILL.md; delete one nothing names.
- Keep every other frontmatter key (`name`, `mcp:`) as it is, and never rename a skill or its directory. Code and other skills name skill paths: `git grep -n <file name>` before you move or delete a file, and fix each namer in the same change.
- Check: `python3 ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...`. Fix each FAIL and each warning. It does not count lines or measure reference files, so also run `wc -l -c` on SKILL.md and each reference.

## 1. Decide that a rule is needed

1. Find the failure in at least two sessions with `tq search` (`using-transcript-store`). One incident turned into one rule is how instruction files triple in size.
2. Run the task in a fresh session with no guidance. Write the rule only if it fails in at least 2 of 3 runs; a rule the model follows anyway costs tokens on every load.
3. Fix the tool, or add a script or a hook, before you write prose: a check a program runs holds where prose does not.
4. Extend a skill that already loads for the task before you create one: four or more skills in one task help less than one to three.
5. If two skills load on the same prompts after one description rewrite, merge them or split their scope; more words will not separate them.

## 2. Write the body

- Choose the form by the failure:
  - The agent lacks a fact (a path, a command, a trap): state the fact.
  - The output has the wrong shape: give its parts in order, or a template. A list of prohibitions produces more unwanted output than no guidance.
  - The agent knows the rule and skips it under an excuse ("the change is small"): state the rule, the excuse and the answer to it.
  - A program can check it: write the check and name the command.
  - The agent already does it in a control run: write nothing.
- Give each rule its reason in one clause, the failure it prevents. A reason that restates the rule does nothing.
- Write a procedure as numbered steps, not narrative.
- Write plain English: short sentences, one idea each, the words the code uses, a verb and an object in every instruction. Not ASD-STE100: a strict rewrite drops facts.
- State the condition that decides. "Unless it matters" or "where reasonable" makes the rule optional.
- Keep the words that set a rule's scope ("before every push", "in Rust files"): models apply a rule only as broadly as it says.
- Check that every path, command and skill you name exists: an agent that follows a dead pointer creates the missing thing.
- Do not copy into a skill the rule to follow the code over a skill: `.claude/CLAUDE.md` holds it.

## 3. Test it

Test every new skill and every edit that changes what an agent does. Read `references/testing.md` before you run a test; it has the arms, the sandbox command and the scoring rules.

1. Take cases from real sessions where the failure happened; invented cases pass where real ones fail.
2. Write the pass bar for each case before the first run.
3. Run each case in a fresh session that gets the raw task, not your diagnosis, and is not told it is a test.
4. Run the old text, the new text and a no-guidance control, 3 times each; 3 more when the arms differ by one or two runs.
5. For a description, also run near-miss prompts that must not load it.
6. Tune on some cases and report on the others; revert a change that improves only the tuned ones.
7. Stop a review loop after three rounds. Check only what changed, and never repeat a finding.

## 4. Remove rules

- To fix a rule agents disobey, delete it and run the same task 3 times with the rest unchanged; rewrite it only if the failure shows in 2 of the 3.
- Delete a rule when its reason no longer holds: a duplicate, written for an older model, obeyed in a control run, one incident that never recurred, or now done by the code or the harness.
- "It never fires" is not a reason by itself: run the control, because deleting ignored rules can remove helpful ones.
- For each required step you remove, name what now makes it happen (a script, a hook, another rule). If nothing does, keep the step: a step left to choice gets skipped.
- Shrink by moving or deleting whole blocks, not by squeezing wording, which drops rules unnoticed.

## 5. Rewrite an existing skill

Read `references/rewriting-a-skill.md` before you rewrite or shorten an existing skill. It has the instruction map and the slop check, which fresh agents run, the behavior test for the most-loaded skills, and the size checks.
