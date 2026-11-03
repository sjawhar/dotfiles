# Testing a skill

## Arms

| Question | Arms |
|---|---|
| Does a new skill help? | with the skill, without it (the no-guidance control) |
| Is an edit better? | old text, new text, and the control |
| Does one line do anything? | the skill with the line, the skill without it |

The control tells you whether the model does the thing anyway. A rule that only restates the model's default passes every test and still costs tokens on every load.

## Running a body test

Give each run the raw task a real session got: the files and the request, with nothing about the skill under test, the expected answer or your diagnosis. A tested agent that knows what is being tested performs for the test.

With subagents (cheap, good for rewrites and other text outputs): one `task` per run, all runs of all arms in one batch in random order, so they share the same conditions. Put the skill text in the run's context as "Follow this skill:" followed by the full text, or omit it for the control. Write outputs to files named by a random ID, and keep the ID-to-arm key apart from the scorer.

A run that gets a real brief, or that needs tools, runs only through the sandbox below, which lets it reach only the model API, write only its own directory, and find HOME, /tmp and /run empty, because a real brief carries absolute paths, live session ids, and push and message steps, so a throwaway directory does not contain it. omp's `--tools read,grep,glob` is no substitute: plugin and MCP tools, Envoy and Slack sends among them, stay callable through `write`.

```bash
run=~/.dotfiles/.secrets/dotfiles/skill-sandbox/run.sh   # in the private dotfiles clone
$run runs/<id> -- timeout 900 omp -p --no-session --mode json --thinking medium --model <model> \
  --append-system-prompt "$(cat /path/to/arm/SKILL.md)" "<raw task>"
```

The transcript lands in `runs/<id>/stdout` and the files the run wrote in `runs/<id>/home`; the script's header lists its options. For the control, leave out `--append-system-prompt`. Start-up takes about 30 to 110 seconds, so run sessions in parallel, alternating arms so a change in model or load hits both.

## Running a description test

Write 8 to 10 prompts that should load the skill and 8 to 10 near misses that share its words but need something else. Make each a real task of two or more sentences, because a one-line request often loads no skill even when the description matches. Run each prompt three times with the command below; `--box-skills` gives the session the skill listing a real session on this box gets:

```bash
$run runs/<id> --box-skills -- timeout 300 omp -p --no-session --mode json --thinking low --model <model> "<prompt>"
grep -c 'skill://<name>' runs/<id>/stdout   # >0: the session read the skill
```

Keep 40% of the prompts aside. Tune the description on the other 60%, then report the held-out rate; pick the version with the best held-out rate, which is often not the last one.

## Scoring

- Write the pass bar, per case, before the first run. A bar chosen after the results is not a bar.
- Score blind: the scorer gets outputs under random IDs and never the arm.
- Use one fresh scorer per output, with the same brief, so one scorer's drift cannot favour an arm. Score against a fixed list (for a rewrite, the old text's instruction list, extracted once).
- Read the transcripts and quote the line that decided each score. A run can pass by accident or fail for a reason that has nothing to do with the skill.
- Report `k/n` per arm and per case, the number of runs and the model, so a reader can judge noise. Treat a difference of one or two runs in six as noise: two identical builds gave 7 of 12 on the same check.
- Drop a pass condition that every arm meets: it cannot separate the arms. The exception is a rewrite's behavior test, where old and new both passing is the expected result.
