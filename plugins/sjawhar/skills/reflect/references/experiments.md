# Experiment readout

This compares sessions with a gated harness feature on against sessions with it off. It is separate from the findings and says nothing about any one failure.

1. Read the gates `~/.dotfiles/omp/extensions/experiments/gates.json` held during the window (`git log -p` over the window). Only a feature set to `random` then can be measured; a feature on or off for every session cannot.
2. Run `scripts/experiments-readout.py` in the order its module docstring gives: its first eval cell, then `scripts/jev-turn-label.py` in bash (that script's docstring has the command), then `await main(...)` in the same kernel. Keep their output files in the run's scratch directory.
3. Report every interval the readout prints, each run, and stop no feature early. An interval that includes 0 is not a wash: a week of traffic detects only large effects.
4. Two text changes that landed within a day of each other cannot be told apart in these numbers. Say so rather than crediting one.
