---
name: writing-agent-skills
description: "Use when you need evidence a change helps (an advisor, a skill, agent instructions, an AGENTS.md rule, a gate), or delete, create or shorten a skill (removing one breaks the code and skills that name it), decide if a lesson becomes a rule, or a skill is too long. Not for human text."
---

# Writing agent skills

Write, test, shorten or delete a skill so that an agent loads it for the right tasks and follows each of its rules.

**Done when:** step 5's check passes the skill with no warning, step 3's test passes for each change to what an agent does, and each file that names a skill you renamed, moved or deleted is fixed.

For an agent guide (AGENTS.md, CLAUDE.md), take its shape and size from `updating-docs`.

Use steps 1 and 3 for an agent guide's rules too.

## Step 1: Decide that a rule is needed

1. **Find it twice.** Find the failure in at least two sessions with `tq search` (`using-transcript-store`): one incident turned into one rule is how instruction files triple in size.
2. **Run a control.** Run the task in a fresh session with no guidance. Write the rule only if it fails in at least 2 of 3 runs: a rule the model follows anyway costs tokens on every load.
3. **Prefer a program.** Fix the tool, or add a script or a hook, before you write prose: a check a program runs holds where prose does not.
4. **Extend first.** Extend a skill that already loads for the task before you create one: four or more skills in one task help less than one to three.
5. **Separate by scope.** If two skills load on the same prompts after one description rewrite, merge them or split their scope: more words will not separate them.

## Step 2: Write the body

**Read `references/style.md` now**, before you write any line of a skill.

Choose the form by the failure:

| The failure | The form |
| --- | --- |
| The agent lacks a fact (a path, a command, a trap) | State the fact |
| The output has the wrong shape | Its parts in order, or a template; a list of prohibitions produces more unwanted output than no guidance |
| The agent knows the rule and skips it under an excuse ("the change is small") | The rule, the excuse and the answer to it |
| A program can check it | The check, and the command that runs it |
| The agent already does it in a control run | Nothing |

- **Use the words the code uses.**
- **Give every instruction a verb and an object.**
- **Write plain English, not ASD-STE100:** a strict rewrite drops facts.
- **State the condition that decides:** "unless it matters" or "where reasonable" makes the rule optional.
- **Keep the words that set a rule's scope** ("before every push", "in Rust files"): models apply a rule only as broadly as it says.
- **Check that each path, command and skill you name exists:** an agent that follows a dead pointer creates the missing thing.
- **Pass `updating-docs`' "The test for every line":** no history, no restated code, no emphasis as force.
- Do not copy into a skill the rule to follow the code over a skill: `~/.dotfiles/.claude/CLAUDE.md` holds it.

## Step 3: Test it

**Read `references/testing.md` now**, before you run any test, a control run included: it has the arms, the sandbox command and the scoring rules.

Read its section "Measuring a harness change" when the change is to the harness (an extension feature, a context line, an advisor prompt) or to a gate or classifier that predicts Sami's decisions, or when you judge text by the fleet's records.

1. **Real cases.** Take cases from real sessions where the failure happened: invented cases pass where real ones fail.
2. **Freeze first.** Before any model call, freeze the cases, the arms and each case's pass bar, so that no result steers them.
3. **Blind runs.** Run each case in a fresh session that gets the raw task, not your diagnosis, and is not told it is a test.
4. **Three arms.** Run the old text, the new text and a no-guidance control 3 times each. Run 3 more of each when the arms differ by one or two runs.
5. **Near misses.** For a description, also run near-miss prompts that must not load it.
6. **Held-out cases.** Before you tune, set aside 40% of the cases (rounded down, at least one), and tune on the rest.
7. **Report the held-out cases:** a variant tuned on the cases you report wins only those.
8. **Tuned-only gains.** Revert a change that improves only the tuned ones.
9. **Three review rounds.** Stop a review loop after three rounds.
10. **Only the change.** In each later round, check only what changed.
11. **No repeats.** Repeat no finding.

## Step 4: Remove rules

| Situation | Do |
| --- | --- |
| Agents disobey a rule | Delete it. Run the same task 3 times with the rest unchanged. Rewrite it only if the failure shows in 2 of the 3 |
| A rule's reason no longer holds: a duplicate, written for an older model, obeyed in a control run, one incident that never recurred, or now done by the code or the harness | Delete it |
| A rule never fires, which is no reason by itself to delete it | Run the control first: deleting ignored rules can remove helpful ones |
| You remove a required step | Name what now makes it happen (a script, a hook, another rule). If nothing does, keep the step: a step left to choice gets skipped |
| A skill is too long | Move or delete whole blocks, not words: squeezing wording drops rules unnoticed |

## Step 5: Check the skill

1. **Run the check:** `uv run --script ~/.dotfiles/plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py <skill dir>...`; `--help` lists each check.
2. **Fix every FAIL and each warning.**

## Step 6: Rewrite an existing skill

**Read `references/rewriting-a-skill.md` now**, before you rewrite or shorten an existing skill: it has the instruction map, the slop check, the behavior test and the size checks.

## Limits

| Part | Limit | `check_skill.py` |
| --- | --- | --- |
| `description` | Starts "Use when", at most 300 characters, says when to load the skill and never its steps, and names the case where the agent thinks it can do without the skill ("also when the diff is pasted inline") | Fails a wrong start or length |
| `SKILL.md` and each markdown file outside `assets/` and `templates/` | At most 8,000 bytes, the whole file counted; no line limit | Fails it |
| Rules in `SKILL.md` (a list item, or a sentence of a paragraph) | At most 40. Over it, split the skill, move blocks into references, or delete rules (step 4): a model obeys fewer of its rules as their number grows | Warns |

- **Keep the steps out of the description:** an agent that reads them there follows them and skips the body.
- **Put the trigger words first:** omp shows the model a hint of about 12 words made from the description, or its first 99 characters until that hint exists.
- **Keep in SKILL.md only the routing and the rules the model would not follow by default.**
- **Put required steps and stop rules first:** a harness that cuts a long file keeps only its start.

| A file or block | Do |
| --- | --- |
| A block that only some tasks need | Move it into `references/<topic>.md`, one level deep. Name it at the step that needs it, with its condition |
| A reference that every task reads | Merge it into SKILL.md when the merged file stays within the limits |
| A reference that nothing names | Delete it |
| A file the skill tells an agent to copy whole into another place (a brief, a form) | Put it under `assets/` when it would go over a limit: the limits do not hold a template. `check_skill.py` reads `templates/` the same way, and only those two directories make a file a template. `assets/` is where the Agent Skills specification puts templates |

- **Before you rename, move or delete a skill, one of its paths or a frontmatter field such as `mcp:`, fix each code, prompt or skill that names it, in the same change:** `git grep -n <name>` finds them.
