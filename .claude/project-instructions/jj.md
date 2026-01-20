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
