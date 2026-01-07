---
description: "Run all quality checks and fix failures"
---

Run all quality checks for this project and fix any failures. Look for and run:

1. **Type checking** (e.g., `pyright`, `basedpyright`, `mypy`, `tsc`)
2. **Linting** (e.g., `ruff check`, `eslint`, `clippy`)
3. **Formatting** (e.g., `ruff format --check`, `prettier --check`, `cargo fmt --check`)
4. **Tests** (e.g., `pytest`, `cargo test`, `npm test`, `go test`)

For each check:
- Run it and capture output
- If it fails, analyze the errors
- Fix one issue at a time
- Re-run to verify the fix
- Continue until all checks pass

If you can't determine the project's tooling, check for config files like `pyproject.toml`, `package.json`, `Cargo.toml`, `Makefile`, or ask.
