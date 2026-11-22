# Rebasing

- A `jj rebase` with no `-r`, `-s` or `-b` means `-b @`: the whole branch, with any commits other sessions stacked on it. `-r` re-parents descendants onto the old parents. `jj rebase --help` has each flag's effect and the diagrams.
- Read `jj log` after every rebase: a rebase that succeeds can record conflicts, and a refusal is not success.
- Do not rebase only because main moved: the rewrite moves descendants and voids the review.
