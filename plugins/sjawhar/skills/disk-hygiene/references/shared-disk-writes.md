# Free space on a shared disk: guards and reservations

Read this before a step that writes more than 2 GB to a shared mount, or before you write a free-space guard for a batch. A lead is a session that runs other agents: its subagents and the worker sessions it starts.

- A free-space guard samples during a batch, not only before it.
- A free-space guard has two thresholds:
  - Start the batch only while free space is at least the mount's floor plus the batch's measured peak.
    - **Why:** the start threshold is the higher one because stopping a batch that has paid its setup costs more than not starting it.
  - Abort what runs when free space falls under the floor.
  - `disk-gate status --mount <mount>` prints the floor.
- When one lead's agents share a disk, one of them reads free space and refuses for all: agents that each read `df` see the same free space at once and all start.
- Across leads, run `disk-gate reserve <gib> <name> --mount <mount>` before the step (without `--mount` it reserves on `/mnt/nvme`).
- Across leads, run `disk-gate release <name>` after the step.
- Size a reservation by the step's measured peak, never by what the step keeps: a build's peak can be three times what it keeps, or more.
- Measure an unmeasured build's peak first, on a real run in a Job in the build cluster (the `running-heavy-jobs` skill), where the scratch is the cluster's.
- From that Job, bring back only the output.
- The header of `~/.dotfiles/scripts/disk-gate` says what it counts and when to use `attach` or `renew`.
