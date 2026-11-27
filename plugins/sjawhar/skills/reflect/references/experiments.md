# Experiment readout

This compares sessions with a gated harness feature on against sessions with it off. It is separate from the findings and says nothing about any one failure.

1. Read the gates `~/.dotfiles/omp/extensions/experiments/gates.json` held during the window (`git log -p` over the window). Only a feature set to `random` then can be measured; a feature on or off for every session cannot.
2. Run `scripts/experiments-readout.py` in the order its module docstring gives, with every file it writes in the run's scratch directory:
   - its first eval cell, with `session_dirs` the omp session directories of every machine Sami worked on in the window (this machine's, and a copy of each other's) and `messages` the window's `messages.jsonl`;
   - `scripts/jev-turn-label.py` in bash (that script's docstring has the command);
   - `await main(...)` in the same kernel, with the Jev output as `jev_labels_path`. Keep `turn_labels_path`, the strong model's label cache, in scratch, so a rerun pays only for new turns.
3. Report every interval the readout prints, each run, and stop no feature early. An interval that includes 0 is not a wash: a week of traffic detects only large effects.
4. Two text changes that landed within a day of each other cannot be told apart in these numbers. Say so rather than crediting one.
