---
name: writing-agent-skills
description: "Use when you need evidence a change helps (an advisor, a skill, agent instructions, an AGENTS.md rule, a gate), or delete, create or shorten a skill (removing one breaks the code and skills that name it), decide if a lesson becomes a rule, or a skill is too long. Not for human text."
---

# Writing agent skills

An agent guide (AGENTS.md, CLAUDE.md) follows `updating-docs` for its shape and size; section 1 below still decides whether a rule goes in it, and section 3 how to test a change to it. Every line of a skill also passes that skill's "The test for every line": no history, no restated code, no emphasis as force. An incident story, a date or a URL goes in the commit message, because the agent reads a story as an instruction and misses its rule.

## Limits

- `description`: starts "Use when", at most 300 characters, says when to load the skill and never its steps: an agent that reads the steps there follows them and skips the body.
- Put the trigger words first, with the case where the agent thinks it can do without the skill ("also when the diff is pasted inline"). omp shows the model a hint of about 12 words made from the description, or its first 99 characters until that hint exists.
- SKILL.md and every other markdown file outside `assets/` and `templates/`: at most 150 lines and 8,000 bytes each, the whole file counted, frontmatter included. These are hard limits: `check_skill.py` fails a file over either.
- SKILL.md holds the routing and the rules the model would not follow by default. Required steps and stop rules go first, because a cut keeps only the start.
- References: in `references/`, one level deep. Move each block that only some tasks need into `references/<topic>.md`, and name it in SKILL.md at the step that needs it, with the condition: "Read `references/<topic>.md` when ...". Merge a reference that every task of the skill reads into SKILL.md when the merged file stays within the limits; delete one nothing names.
- A file the skill tells an agent to copy whole into another place (a brief, a form) may go under `assets/`, where the Agent Skills specification puts templates; `check_skill.py` reads `templates/` the same way. That directory alone makes the file a template, and the limits do not hold a template.
- Code, prompts and other skills name skills and their paths, and code reads some frontmatter (`mcp:`): `git grep -n <name>` before you rename, move or delete one, and fix each namer in the same change.
- Check: `uv run --script ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...`; `--help` lists each check. Fix every FAIL and each warning. Over 40 rule lines (each bullet or numbered line of SKILL.md's body) it warns: split the skill, move blocks into references, or delete rules (section 4), because a model obeys fewer of its rules as their number grows.

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
- Do not copy into a skill the rule to follow the code over a skill: `~/.dotfiles/.claude/CLAUDE.md` holds it.

## 3. Test it

Test every new skill and every edit that changes what an agent does. Read `references/testing.md` before you run a test; it has the arms, the sandbox command and the scoring rules. Read its section "Measuring a harness change" when the change is to the harness (an extension feature, a context line, an advisor prompt) or to a gate or classifier that predicts Sami's decisions, or when you judge text by the fleet's records.

1. Take cases from real sessions where the failure happened; invented cases pass where real ones fail.
2. Before any model call, freeze the cases, the arms and the grading (each case's pass bar): results must not steer them.
3. Run each case in a fresh session that gets the raw task, not your diagnosis, and is not told it is a test.
4. Run the old text, the new text and a no-guidance control, 3 times each; 3 more when the arms differ by one or two runs.
5. For a description, also run near-miss prompts that must not load it.
6. Before you tune, set aside 40% of the cases (rounded down, at least one). Tune on the rest and report the held-out ones, because a variant tuned on the cases you report wins only those. Revert a change that improves only the tuned ones.
7. Stop a review loop after three rounds. Check only what changed, and never repeat a finding.

## 4. Remove rules

- To fix a rule agents disobey, delete it and run the same task 3 times with the rest unchanged; rewrite it only if the failure shows in 2 of the 3.
- Delete a rule when its reason no longer holds: a duplicate, written for an older model, obeyed in a control run, one incident that never recurred, or now done by the code or the harness.
- "It never fires" is not a reason by itself: run the control, because deleting ignored rules can remove helpful ones.
- For each required step you remove, name what now makes it happen (a script, a hook, another rule). If nothing does, keep the step: a step left to choice gets skipped.
- Shrink by moving or deleting whole blocks, not by squeezing wording, which drops rules unnoticed.

## 5. Rewrite an existing skill

Read `references/rewriting-a-skill.md` before you rewrite or shorten an existing skill. It has the instruction map and the slop check, which fresh agents run, the behavior test for the most-loaded skills, and the size checks.
