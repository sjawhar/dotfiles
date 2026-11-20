# Testing a skill

## Arms

- Does a new skill help? With the skill, and without it (the no-guidance control).
- Is an edit better? The old text, the new text, and the control.
- Does one line do anything? The skill with the line, and the skill without it.

The control shows whether the model does the thing anyway: a rule that restates the model's default passes every test and still costs tokens on every load.

## Running a body test

Give each run the raw task a real session got: the files and the request, with nothing about the skill under test, the expected answer or your diagnosis.

A case where the model only says which tool calls it would make measures stated intent, not behavior, and does not decide adoption.

With subagents (cheap; good for rewrites and other text outputs): one `task` per run, all runs of all arms in one batch in random order, so they share the same conditions. Put the skill text in the run's context as "Follow this skill:" and the full text, or leave it out for the control. Write outputs to files named by a random ID, and keep the ID-to-arm key away from the scorer.

A run that gets a real brief, or needs tools, runs only in the sandbox below. It reaches only the model API, writes only its own directory, and finds HOME, /tmp and /run empty, because a real brief carries absolute paths, live session ids, and push and message steps. omp's `--tools read,grep,glob` is no substitute: plugin and MCP tools, Envoy and Slack sends among them, stay callable through `write`.

```bash
run=~/.dotfiles/.secrets/dotfiles/skill-sandbox/run.sh   # in the private dotfiles clone
$run runs/<id> -- timeout 900 omp -p --no-session --mode json --thinking medium --model <model> \
  --append-system-prompt "$(cat /path/to/arm/SKILL.md)" "<raw task>"
```

The transcript lands in `runs/<id>/stdout` and the files the run wrote in `runs/<id>/home`; the script's header lists its options. For the control, leave out `--append-system-prompt`. Run sessions in parallel, interleaving the arms so a change in model or load hits both.

## Running a description test

Write 8 to 10 prompts that should load the skill and 8 to 10 near misses that share its words but need something else. Make each a real task of two or more sentences: a one-line request often loads no skill even when the description matches. Run each prompt three times; `--box-skills` gives the session the skill listing a real session on this box gets:

```bash
$run runs/<id> --box-skills -- timeout 300 omp -p --no-session --mode json --thinking low --model <model> "<prompt>"
grep -c 'skill://<name>' runs/<id>/stdout   # more than 0: the session read the skill
```

Keep 40% of the prompts aside. Tune the description on the other 60%, then report the held-out rate. Pick the version with the best held-out rate, which is often not the last one.

## Scoring

- Score blind: the scorer gets outputs under random IDs and never the arm.
- Use one fresh scorer per output, with the same brief, so one scorer's drift cannot favour an arm. Score against a fixed list (for a rewrite, the old text's instruction list, extracted once).
- Read each transcript and quote the line that decided its score: a run can pass by luck or fail for a reason unrelated to the skill.
- A run that ended in a provider or grader error is not a score: a zero penalizes the wrong arm.
- Report `k/n` per arm and per case with its 95% interval, the number of runs and the model, not only the difference between arms. Treat a difference of one or two runs in six as noise.
- Drop a pass condition every arm meets: it cannot separate the arms. The exception is a rewrite's behavior test, where old and new both passing is the expected result.

## Measuring a harness change

Pick the route by what the change changes.

- Harness behavior you can switch per session (an extension feature, a context line, an advisor): measure it on historical sessions, not live ones, so every gate committed to `~/.dotfiles/omp/extensions/experiments/gates.json` stays `on` or `off`, never `random`, which draws the feature on or off in each of Sami's live sessions.
  - `reflect`'s weekly experiment readout (`skill://reflect/references/experiments.md`, which runs `experiments-readout.py`) compares sessions with a feature on against sessions with it off, over a window in which its gate was `random`. For each outcome (the share of Sami's turns that correct the agent, the share of merged pull requests that the delivery dashboard marks as rework, spend per merged pull request) it gives the difference with a 95% interval over the sessions that drew each arm. A week of traffic detects only large effects: report the interval, never "no effect".
- Skill or instruction text: use the draft in your next matching task and fix each step it gets wrong. To show that it changes behavior, run the three arms of SKILL.md "3. Test it". To judge it on the fleet's records, compare the rate per 100 records of the failure type the change targets in `reflect`'s `counts.json`, before and after the change.
- Agreement with Sami's own labels (a gate or classifier predicting his decision): re-run the stored population with its `score.py` in the private `sjawhar/agent-eval-data` (`experiments/askgate/`, `experiments/forkgate/`, `experiments/sami-proxy/eval/`). Report agreement with an interval, from a score that uses no LLM judge.
- Scenario runs, in which fresh agents each do a task a skill governs and each run is scored from what its agent wrote: cybermonklabs/legion holds a runner in `packages/pi-legion/scripts/skill-scenarios/` that compares two checkouts' versions of a skill; `rig.sh`'s header gives its commands, the number of runs among them. Where `packages/pi-legion/scripts/skill-cuts/` exists, its README gives a runner that tests whether a cut of a skill's text keeps what agents do with it. Read both with `gh api repos/cybermonklabs/legion/contents/<path>`.
  - A prompt that neither runner covers: build its runner in the repository whose prompt it tests, so it gates that repository's pull requests: `omp --mode rpc` with the production prompt, tools that record each Dispatch, Envoy and Legion call instead of making it, repeated trials, and a control run on the unchanged prompt.
- The archived `agent-evals` runs, cases and comparison code are in `sjawhar/agent-eval-data`; reproduce an archived comparison with `uv run --project <clone> agent-evals compare --run-dir <run>`.
