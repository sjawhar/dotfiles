# jj-vcs/jj Contribution Guidelines

Guidelines derived from PR review feedback.

## Code Organization

- **Import functions by name** rather than using module prefixes.
- **Keep Args structs close to their consumer functions** - don't place helper functions between them.
- **Extend existing functions** rather than adding logic at call sites.
- **Move checks inside existing conditionals** when they logically belong there.

## API and Library Usage

- **Prefer gix APIs over git CLI** - add TODO comments when falling back to CLI due to missing gix features.
- Use `PathBuf::from()` instead of `Path::new().to_path_buf()`.

## Atomicity and Error Handling

- **Filesystem operations must be atomic** - failures should not leave the workspace in a dirty state.
- **Use RAII guards for cleanup** on failure (e.g., `GitWorktreeGuard` that removes resources if later operations fail).
- **Perform destructive operations last** - validate and do non-destructive work first.
- **Commit transactions before external cleanup** - if external cleanup fails after transaction commits, warn but don't fail.

## Output and Messages

- **Use plural forms** for messages (standardized for future i18n).
- Use lowercase "jj" not "JJ".
- **Show useful information by default** rather than hiding behind `--verbose` flags.
- Show errors even with `--quiet` - only suppress optional/informational output.

## Function Signatures

- **Use explicit boolean flags** when the flag's meaning matters - don't use `Option<T>` to indicate modes when a boolean is clearer.
- Remove unnecessary `Option` wrappers when the value is always present.

## Testing

- **Don't add tests that duplicate existing coverage** - if existing tests exercise a code path, new tests aren't needed.
- **Don't extensively test thin wrappers** around gix APIs.
- **CLI tests are slow** - minimize them.
- **Assert on actual outcomes, not just success** - tests must verify data was correctly created/modified, not just that the command didn't error.
- Use bare repos in test fixtures and `.to_str().unwrap()` where path content matters.

## Documentation

- **Design docs aren't needed for existing features** - use the project's blueprint format if adding them.
- **Move PR descriptions into commit messages** so information survives if the repo moves off GitHub.

## PR Strategy

- Split large features into a **stack of independently reviewable PRs**.
- Reference the full stack in each PR description.

## Quality Assurance Workflow (CRITICAL)

### Before Every Push

Run these commands to avoid CI failures:
```bash
cargo +nightly fmt --all
cargo clippy --workspace --all-targets
cargo test -p jj-cli -- <your_test_pattern>
```

Never use `cargo fmt` alone (must use nightly). Run tests relevant to your changes.

### PR Stack Workflow

When working with a stack of PRs/commits:

1. **Process each commit individually** - check out each commit, run all quality checks, then push
2. **Verify tests belong to the right commit** - tests should not leak between PRs
3. **Each PR must be independently reviewable** - all tests for a feature belong with that feature's commit

### After Pushing

1. **Check CI status** - don't assume success, verify on GitHub
2. **Address ALL review comments** before pushing subsequent changes
3. **Re-run local tests** after addressing review feedback

### GitHub Interactions

- **Never post comments to GitHub** (PR comments, issue comments, review comments) without explicit user approval
- Always show the user what you plan to post and wait for confirmation

## Existing Patterns to Follow

- **Git CLI commands that parse output** must set `LC_ALL=C`:
  ```rust
  Command::new("git")
      .env("LC_ALL", "C")  // Required for parsing output
      .args(["worktree", "list", "--porcelain"])
  ```
  See `lib/src/git_subprocess.rs` for the pattern.

- **Check existing utilities** before implementing new functionality
- **Match existing error message styles** and hint patterns

## Project Guidelines
Unlike many GitHub projects (but like many VCS projects), we care more about the contents of commits than about the contents of PRs. We review each commit separately, and we don't squash-merge the PR (so please manually squash any fixup commits before sending for review).

Each commit should ideally do one thing. For example, if you need to refactor a function in order to add a new feature cleanly, put the refactoring in one commit and the new feature in a different commit. If the refactoring itself consists of many parts, try to separate out those into separate commits. You can use jj split to do it if you didn't realize ahead of time how it should be split up. Include tests and documentation in the same commit as the code they test and document.

The commit message should describe the changes in the commit; the PR description can even be empty, but feel free to include a personal message. We don't use Conventional Commits and instead start the commit message with <topic>: rather than like chore:, feat: and fix:. This means if you modified a command in the CLI, use its name as the topic, e.g. next/prev: <your-modification> or conflicts: <your-modification>. We don't currently have a specific guidelines on what to write in the topic field, but the reviewers will help you provide a topic if you have difficulties choosing it. How to Write a Git Commit Message is a good guide if you're new to writing good commit messages. We are not particularly strict about the style, but please do explain the reason for the change unless it's obvious.

## Writing Good Commit Messages
_Keep in mind: This has all been said before._
1. Separate subject from body with a blank line
2. Limit the subject line to 50 characters
3. Capitalize the subject line
4. Do not end the subject line with a period
5. Use the imperative mood in the subject line
6. Wrap the body at 72 characters
7. Use the body to explain what and why vs. how
