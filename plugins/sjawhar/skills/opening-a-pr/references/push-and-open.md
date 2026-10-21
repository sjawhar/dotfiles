# Step 11: pushing and opening

## One line of work, one PR

- Look for this session's PR first: `git branch -vv`, `gh pr view 2>/dev/null`, `gh pr list --author @me`. Fold same-line work in with `git push`, then `gh pr edit`. No sibling one-off PRs.
- Use `gh-stack` only for dependent work that lands as one reviewed unit. Keep an unmerged base small, merge it fast, and split off what does not need it.
- Size the PR before review: split where each piece can be verified alone. Ship the pieces in dependency order and rebase the rest onto each merged one. An unused slice can ship dark if production stays unbroken.
- Fix correctness findings before merge. Push once per review round.
- With no PR yet:

  ```bash
  git checkout -b <name> && git push -u origin <name>
  gh pr create --head <name>   # closing keywords for the issues it resolves
  gh pr list --head <name>     # read the URL here, not from the create output
  ```

- In a box or worktree that is not your shell's own, use `gh -R <owner>/<repo>`. Never export `GIT_DIR` or `GIT_WORK_TREE`.

## Which checks

- Run the fast steps of the CI workflow that gates the changed paths: lint, type, package-local unit lanes. Add browser or e2e suites for a frontend. Skip the full matrix, deploys and image builds.
- Scope: the changed packages AND every test file that imports a changed module:

  ```bash
  grep -rl '<module names>' tests | xargs pytest -q
  ```

- A skipped CI lane is a gap only when it skips because it is broken.

## Outside the agent shell

```bash
env -i HOME="$HOME" USER="$USER" PATH="$PATH" \
  CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}" RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}" \
  <command>
```

- omp exports variables that a runner does not have, such as `SECRETSD_SESSION_TOKEN_FILE`. `env -u <VARIABLE> <command>` reproduces a failure that one of them hides.
- Anything that touches AWS or Bedrock: add `AWS_EC2_METADATA_DISABLED=true`. Otherwise the devbox instance role signs the calls.
- After red CI, run the whole suite this way with `--no-fail-fast` before you push a fix.

## Labels

- A label at creation fires `opened` and `labeled` together and doubles each `labeled` workflow.
- Do not add labels that CI applies itself. Add a needed label only after the head's checks settle.

## From a jj workspace

The push leaves HEAD detached, so `gh pr create` without `--head` prints a jj hint and no URL.
