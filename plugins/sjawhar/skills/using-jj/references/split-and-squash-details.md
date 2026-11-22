# Splitting and squashing

- Give `jj split` and `jj squash` `-m`, or `squash -u`: without it they open an editor, which fails with no terminal.
- Do not take paths out of a merge commit with `split` or `squash --from`: a merge's paths are its resolution, so they vanish from the merged tree. Keep the work in the merge, or rebuild the delta on a fresh child.
