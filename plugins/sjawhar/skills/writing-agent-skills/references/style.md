# Style of a skill

Write a skill that an agent scans for its next action, in the style that superpowers (v7.0.1) and compound-engineering (8a776c13) share.

**Done when:** the skill follows each rule below, and a fresh reader given only the skill and this file finds no rule broken.

Each rule gives an example and a line of either set that shows it. `sp:` is superpowers' `skills/`, at v7.0.1. `ce:` is compound-engineering's repository at 8a776c13, its `skills/` unless the path says `.agents/`.

## The parts of a SKILL.md, in order

| Part | Holds |
| --- | --- |
| `# Title`, then one or two sentences | The goal: what is true when the skill has worked |
| `**Done when:**` | The check that ends the skill |
| `## Step 1: <verb> <object>`, or `## 1. <name>` | The procedure, as numbered steps |
| A table | Facts that share their attributes, such as situation and action |
| `## References` | Each reference with its condition, where no step names it |

## Rules

1. **Goal first, then the check that ends the skill.** Open with the goal in one or two sentences. Put a `**Done when:**` line under it.
   - Example: `Replace a leaked key with no downtime.` then `**Done when:** the old key is revoked and each service answers with the new one.`
   - Source: sp `verification-before-completion/SKILL.md:10` ("**Core principle:** Evidence before claims, always."); ce `ce-code-review/SKILL.md:11` ("**Done when:** required review and validation are complete").
2. **A procedure is numbered steps, each starting with its name.**
   - Example: `1. **Reproduce.** Run the failing command and save its output.`
   - Source: sp `systematic-debugging/SKILL.md:52` ("1. **Read Error Messages Carefully**"); ce `ce-noslop/SKILL.md:37` ("1. **Mechanism.** Does the sentence say what the thing does").
3. **One rule to a list item.** Start the item with the rule, or with a bold label, and give the detail after it.
   - Example: `- **Read before you edit.** Open the file and its callers first.`
   - Source: sp `using-git-worktrees/SKILL.md:26` ("**Submodule guard:** ..."); ce `ce-code-review/SKILL.md:37` ("- **Report-only by default; never push.**").
4. **Short sentences, one idea each.** Split a sentence at its second rule.
   - Example: `Write the test first. Watch it fail.`, not `Write the test first and watch it fail, which proves the test can fail before any code exists.`
   - Source: sp `test-driven-development/SKILL.md:10` ("Write the test first. Watch it fail. Write minimal code to pass."); ce `.agents/skills/ce-skill-work/SKILL.md:33` ("Write one idea per sentence in ordinary English.").
5. **A reason only where the rule would look arbitrary.** A rule that is plain stands bare. Give a reason as one clause after the rule, or as `**Why:**`.
   - Example: `Run the tests before you push.` stands bare. `Pin the version: a floating tag changes between two runs.` needs its reason.
   - Source: ce `.agents/skills/ce-skill-work/SKILL.md:28` ("Rationale after a directive that stands alone ... do not."); sp `using-git-worktrees/SKILL.md:88` ("**Why critical:** Prevents accidentally committing worktree contents to repository.").
6. **A table for facts that share their attributes.** Two or more cases with the same columns go in a table: situation and action, code and meaning, claim and evidence.
   - Example: `| Exit code | Do |` with rows `| 0 | Go on |` and `| 2 | Fix the arguments |`.
   - Source: sp `using-git-worktrees/SKILL.md:144-146` ("| Situation | Action |", "| Already in linked worktree | Skip creation (Step 0) |").
7. **One example for each shape of command or output.** Give a command or a template once, with its parameters. Use a good and a bad example only where the bad form is the common one.
   - Example: `uv run --script check_skill.py <skill dir>...`, once, not once for each skill.
   - Source: sp `writing-skills/SKILL.md:113` ("**One excellent example beats many mediocre ones**"); ce `.agents/skills/ce-skill-work/SKILL.md:32` ("If two recipes share the same command skeleton, write one recipe with parameters or deltas").
8. **The imperative, to "you".** Say who acts and what they do. Describe the system only where the agent acts on the fact.
   - Example: `Run the migration.`, not `The migration should be run.`
   - Source: sp `test-driven-development/SKILL.md:10`; ce `ce-simplify-code/SKILL.md:18` ("Resolve the scope in this order:").
9. **No story.** A date, an incident or a design history goes in the commit message. The skill keeps the rule it taught.
   - Example: `Run the migration before the deploy.`, not `On 11-03 a deploy failed because the migration had not run.`
   - Source: sp `writing-skills/SKILL.md:315-317` (the narrative example "In session 2025-10-03, we found empty projectDir caused..." and "**Why bad:** Too specific, not reusable").
10. **Each reference named at the step that needs it, with its condition.**
    - Example: `**Read references/testing.md now** when the change alters what an agent does.`
    - Source: sp `test-driven-development/SKILL.md:216` ("When writing or changing any test, read [writing-good-tests.md](writing-good-tests.md)"); ce `ce-debug/SKILL.md:48` ("**Read `references/investigate.md` now and follow it for Phases 0-2**").

Bold marks a rule's label, never its strength. No capitals for force.
