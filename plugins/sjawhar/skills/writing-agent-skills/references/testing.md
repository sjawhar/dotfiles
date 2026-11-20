# Testing a skill

## Arms

- Does a new skill help? With the skill, and without it (the no-guidance control).
- Is an edit better? The old text, the new text, and the control.
- Does one line do anything? The skill with the line, and the skill without it.

The control shows whether the model does the thing anyway: a rule that restates the model's default passes every test and still costs tokens on every load.

## Running a body test

Give each run the raw task a real session got: the files and the request, with nothing about the skill under test, the expected answer or your diagnosis.

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
- Report `k/n` per arm and per case, the number of runs and the model. Treat a difference of one or two runs in six as noise.
- Drop a pass condition every arm meets: it cannot separate the arms. The exception is a rewrite's behavior test, where old and new both passing is the expected result.
