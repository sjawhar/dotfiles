# Style of a skill

Write a skill that an agent scans for its next action, in the style that superpowers (v7.0.1) and compound-engineering (8a776c13) share.

**Done when:** a fresh reader given only the skill and this file finds each rule below followed.

Each rule has an example and a line of either set that shows it. `sp:` is superpowers' `skills/` at v7.0.1. `ce:` is compound-engineering's repository at 8a776c13, under `skills/` unless the path starts `.agents/`.

## The parts of a SKILL.md, in order

The table puts rules 1, 2, 6 and 10 in order.

| Part | Holds |
| --- | --- |
| `# Title`, then one or two sentences | The goal: what is true when the skill has worked |
| `**Done when:**` | The check that ends the skill |
| `## Step 1: <verb> <object>`, or `## 1. <name>` | The procedure, as numbered steps |
| A table | Facts that share their attributes, such as situation and action |
| `## References` | Each reference with its condition, where no step names it |

## Rules

1. **Open with the goal in one or two sentences, then a `**Done when:**` line.**
   - Example: `Replace a leaked key with no downtime.` then `**Done when:** the old key is revoked and each service answers with the new one.`
   - Source: sp `verification-before-completion/SKILL.md:10` ("**Core principle:** Evidence before claims, always."); ce `ce-code-review/SKILL.md:11` ("**Done when:** required review and validation are complete"); ce `.agents/skills/ce-skill-work/SKILL.md:31` ("**One done bar first.** Every skill needs a skill-level done condition.").
2. **Write a procedure as numbered steps, each starting with its name.**
   - Example: `1. **Reproduce.** Run the failing command and save its output.`
   - Source: sp `systematic-debugging/SKILL.md:52` ("1. **Read Error Messages Carefully**"); ce `ce-noslop/SKILL.md:37` ("1. **Mechanism.** Does the sentence say what the thing does").
3. **Put one rule in a list item, starting with the rule or with a bold label.** Its detail follows: how, when, the exception, the reason.
   - Example: `- **Read before you edit.** Open the file and its callers first.`
   - Source: sp `using-git-worktrees/SKILL.md:26` ("**Submodule guard:** `GIT_DIR != GIT_COMMON` is also true inside git submodules."); ce `ce-code-review/SKILL.md:37` ("- **Report-only by default; never push.** A bare `ce-code-review` invocation produces findings and does not apply them.").
4. **Write short sentences, one idea each.** Split a sentence at its second rule.
   - Example: `Write the test first. Watch it fail.`, not `Write the test first and watch it fail, which proves the test can fail before any code exists.`
   - Source: sp `test-driven-development/SKILL.md:10` ("Write the test first. Watch it fail. Write minimal code to pass."); ce `.agents/skills/ce-skill-work/SKILL.md:33` ("Write one idea per sentence in ordinary English.").
5. **Give a reason only where the rule would look arbitrary:** one clause after the rule, or a `**Why:**` line, that names the failure the rule prevents.
   - Example: `Run the tests before you push.` stands bare. `Pin the version: a floating tag changes between two runs.` needs its reason.
   - Source: ce `.agents/skills/ce-skill-work/SKILL.md:28` ("Rationale after a directive that stands alone, effort language, and capability restatement do not."); sp `using-git-worktrees/SKILL.md:88` ("**Why critical:** Prevents accidentally committing worktree contents to repository.").
6. **Put facts that share their attributes in a table:** situation and action, code and meaning, claim and evidence.
   - Example: `| Exit code | Do |` with the rows `| 0 | Go on |` and `| 2 | Fix the arguments |`.
   - Source: sp `using-git-worktrees/SKILL.md:144-146` ("| Situation | Action |", "| Already in linked worktree | Skip creation (Step 0) |").
7. **Give one example for each shape of command or output, with its parameters.** Add a bad example only where the bad form is the common one.
   - Example: `uv run --script check_skill.py <skill dir>...`, once, not once for each skill.
   - Source: sp `writing-skills/SKILL.md:113` ("**One excellent example beats many mediocre ones**"); ce `.agents/skills/ce-skill-work/SKILL.md:32` ("If two recipes share the same command skeleton, write one recipe with parameters or deltas").
8. **Write in the imperative, to "you".** Describe the system only where the agent acts on the fact.
   - Example: `Run the migration.`, not `The migration should be run.`
   - Source: sp `test-driven-development/SKILL.md:10` ("Write the test first."); ce `ce-simplify-code/SKILL.md:18` ("Resolve the scope in this order:").
9. **Tell no story:** put a date, an incident, a URL or a design history in the commit message, because the agent reads a story as an instruction and misses its rule.
   - Example: `Run the migration before the deploy.`, not `On 11-03 a deploy failed because the migration had not run.`
   - Source: sp `writing-skills/SKILL.md:315-317` (the narrative example "In session 2025-10-03, we found empty projectDir caused..." and "**Why bad:** Too specific, not reusable").
10. **Name each reference at the step that needs it, with its condition.**
    - Example: `**Read references/testing.md now** when the change alters what an agent does.`
    - Source: sp `test-driven-development/SKILL.md:216` ("When writing or changing any test, read [writing-good-tests.md](writing-good-tests.md)"); ce `ce-debug/SKILL.md:48` ("**Read `references/investigate.md` now and follow it for Phases 0-2**").
