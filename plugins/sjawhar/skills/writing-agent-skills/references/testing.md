# Testing a skill

Measure whether a skill, an edit or one line changes what an agent does.

**Done when:** each arm has run on frozen cases, a blind scorer has scored each run, and the report gives each result with its interval: `k/n` per arm and per case for a body test, the held-out rate for a description test.

## Arms

| Question | Arms |
| --- | --- |
| Does a new skill help? | With the skill, and without it (the no-guidance control) |
| Is an edit better? | The old text, the new text, and the control |
| Does one line do anything? | The skill with the line, and the skill without it |

**Why a control:** it shows whether the model does the thing anyway. A rule that restates the model's default passes every test and still costs tokens on every load.

## Running a body test

1. **Give each run the raw task a real session got:** the files and the request, with nothing about the skill under test, the expected answer or your diagnosis.
2. **Test what the model does.** A case where it only says which tool calls it would make measures stated intent, which does not decide whether to adopt the text.
3. **Pick the runner:**

   | The run | Runner |
   | --- | --- |
   | Needs no tools and gets no real brief (a rewrite, another text output) | Subagents, which cost little: one `task` per run |
   | Gets a real brief, or needs tools | Only the sandbox below, because a real brief carries absolute paths, live session ids, and push and message steps |

4. **Batch the subagents.** Send all runs of all arms in one batch, in random order, so that they share the same conditions.
5. **Give each subagent its arm:** "Follow this skill:" and the full text in its context, or no skill for the control.
6. **Hide the arms from the scorer.** Name each output file by a random ID. Keep the ID-to-arm key away from the scorer.
7. **Run the sandbox for a real brief:** it reaches only the model API, writes only its own directory, and finds HOME, /tmp and /run empty.
8. **Do not use omp's `--tools read,grep,glob` in its place:** plugin and MCP tools, Envoy and Slack sends among them, stay callable through `write`.
9. **Run each arm with the settings of its test** (below), in this command:

   ```bash
   run=~/.dotfiles/.secrets/dotfiles/skill-sandbox/run.sh   # in the private dotfiles clone
   $run runs/<id> [--box-skills] -- timeout <seconds> omp -p --no-session --mode json --thinking <level> --model <model> \
     [--append-system-prompt "$(cat /path/to/arm/SKILL.md)"] "<raw task or prompt>"
   ```

   | Test | `timeout` | `--thinking` | `--box-skills` | `--append-system-prompt` |
   | --- | --- | --- | --- | --- |
   | Body test, skill arm | 900 | medium | no | the arm's SKILL.md |
   | Body test, control | 900 | medium | no | none |
   | Description test | 300 | low | yes: the skill listing a real session on this box gets | none |

   The transcript lands in `runs/<id>/stdout`, and the files the run wrote in `runs/<id>/home`. The script's header lists its options.
10. **Interleave the arms.** Run sessions in parallel, with the arms interleaved, so that a change in model or load hits each arm.

## Running a description test

1. **Write the prompts.** Write 8 to 10 prompts that should load the skill and 8 to 10 near misses that share its words but need something else. Make each a real task of two or more sentences: a one-line request often loads no skill even when the description matches.
2. **Run each prompt three times** in the sandbox, with the description test's settings.
3. **Count the reads:** `grep -c 'skill://<name>' runs/<id>/stdout` is more than 0 when the session read the skill.
4. **Tune on 60%.** Keep 40% of the prompts aside. Tune the description on the rest.
5. **Report the held-out rate.**
6. **Pick the version with the best held-out rate**, which is often not the last one.

## Scoring

- **Score blind:** give the scorer outputs under random IDs, never the arm.
- **Use one fresh scorer per output**, with the same brief, so that one scorer's drift cannot favour an arm.
- **Score against a fixed list:** for a rewrite, the old text's instruction list, extracted once.
- **Quote the line that decided each score:** a run can pass by luck or fail for a reason unrelated to the skill.
- **Count no score for a run that ended in a provider or grader error:** a zero penalizes the wrong arm.
- **Report `k/n` per arm and per case with its 95% interval**, the number of runs and the model, not only the difference between arms.
- **Treat a difference of one or two runs in six as noise.**
- **Drop a pass condition every arm meets:** it cannot separate the arms. The exception is a rewrite's behavior test, where old and new both passing is the expected result.

## Measuring a harness change

Pick the route by what the change changes:

| The change | Route |
| --- | --- |
| Harness behavior you can switch per session (an extension feature, a context line, an advisor) | Measure it on historical sessions, not live ones, with `reflect`'s weekly experiment readout (below). Commit no gate in `~/.dotfiles/omp/extensions/experiments/gates.json` as `random`, which draws the feature on or off in each of Sami's live sessions |
| Skill or instruction text | Use the draft in your next matching task. Fix each step it gets wrong. To show that it changes behavior, run the three arms of SKILL.md, "Step 3: Test it". To judge it on the fleet's records, compare the rate per 100 records of its failure type in `reflect`'s `counts.json`, before and after the change |
| A gate or classifier that predicts Sami's decision | Re-run the stored cases with their `score.py` in the private `sjawhar/agent-eval-data` (`experiments/askgate/`, `experiments/forkgate/`, `experiments/sami-proxy/eval/`). Report agreement with an interval, from a score that uses no LLM judge |
| A prompt that fresh agents follow, each run scored from what its agent wrote | Use cybermonklabs/legion's runner in `packages/pi-legion/scripts/skill-scenarios/`, which compares two checkouts' versions of a skill. `rig.sh`'s header gives its commands and the number of runs. Where `packages/pi-legion/scripts/skill-cuts/` exists, use the runner its README gives, which tests whether a cut of a skill's text keeps what agents do with it. Read both with `gh api repos/cybermonklabs/legion/contents/<path>` |
| A prompt that neither runner covers | Build its runner in the repository whose prompt it tests, so that it gates that repository's pull requests. Give it `omp --mode rpc` with the production prompt, tools that record each Dispatch, Envoy and Legion call instead of making it, repeated trials, and a control run on the unchanged prompt |

The readout (`skill://reflect/references/experiments.md`, which runs `experiments-readout.py`) compares sessions with a feature on against sessions with it off, over a past window in which the feature's committed gate was `random`. It cannot measure a feature that was `on` or `off` for every session of the window. For each outcome (the share of Sami's turns that correct the agent, the share of merged pull requests that the delivery dashboard marks as rework, spend per merged pull request) it gives the difference with a 95% interval.

- **Find the window** with `git log -p` on `gates.json`.
- **Report the readout's interval, never "no effect":** a week of traffic detects only large effects.

To reproduce an archived `agent-evals` comparison, use `sjawhar/agent-eval-data`, which holds the runs, the cases and the comparison code: `uv run --project <clone> agent-evals compare --run-dir <run>`.
