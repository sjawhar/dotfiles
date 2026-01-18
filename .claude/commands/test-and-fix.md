---
description: "Run all quality checks and fix failures"
---

Run all quality checks for this project and fix any failures.

**1. Discover tooling:**
- Check for `pyproject.toml`, `package.json`, `Cargo.toml`, `Makefile`, or similar
- If unclear which tools to run, ask the user

**2. Run checks in order** (fix each category before moving to the next):

1. **Formatting** (e.g., `ruff format --check`, `prettier --check`, `cargo fmt --check`)
   - Fix first since it's mechanical and may affect other checks
2. **Linting** (e.g., `ruff check`, `eslint`, `clippy`)
   - Batch similar errors for efficiency
3. **Type checking** (e.g., `pyright`, `mypy`, `tsc`)
   - Fix type errors
4. **Tests** (e.g., `pytest`, `cargo test`, `npm test`, `go test`)
   - Run affected tests first if possible, then full suite

**3. For each failing check:**
- Run and capture output
- Group similar errors (e.g., all "missing type annotation" errors)
- Fix the group, then re-run to verify
- If a fix introduces new failures, revert and try a different approach

**4. Stopping conditions:**
- **Success:** All checks pass
- **Blocked:** After 3 attempts on the same error, stop and ask for guidance
- **Timeout:** If fixing for > 20 minutes without progress, summarize and pause

**5. Report:**
Summarize what was fixed and what (if anything) remains.

**Done when:** All checks pass, or you've reported blockers.
