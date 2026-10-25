# Experiment readout

Read this when a harness feature ran behind a per-session gate in the window, and the run has to say whether it helped. It is a separate measurement from the findings: it compares sessions with the feature on against sessions with it off, and says nothing about any one failure.

1. Read `~/.dotfiles/omp/extensions/experiments/gates.json` for the features set to `random` in the window, which gave each session the feature on or off by chance. A feature that was on for every session cannot be measured here.
2. Label Sami's turns with Jev (the typesafe-ai classifier `scripts/jev-turn-label.py` calls), and send the turns it leaves unlabelled to the strong model. The exact commands are in that script's module docstring:
   - in an eval Python cell, `%load scripts/experiments-readout.py` and run `dump_turns_for_jev` over the session directories of sami-rocqperf and sami-leanperf and the window's message file;
   - in bash, run `scripts/jev-turn-label.py` with its API key from the secrets broker (`skill://using-secrets`) on those turns;
   - in the same kernel, `await main(...)` with the Jev output as `jev_labels_path`. Keep the strong-model turn-label cache in the scratch directory, so a rerun pays only for new turns.
3. Report three 95% intervals per feature: share of Sami's turns that correct the agent, share of merged PRs the delivery dashboard marks as rework, model spend per merged PR.
4. Report every interval each run, and stop no feature early. An interval that includes 0 keeps the 50/50 assignment: it is not a wash, because a week of traffic detects only large effects.
5. Two text changes that landed within a day of each other cannot be told apart in these numbers. Say so rather than crediting one.
