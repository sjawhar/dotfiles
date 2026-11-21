# Acceptance

## The scenario in every brief

- Every worker brief carries its slice's full acceptance scenario: existing data, the real user and transport (the client the user reaches the change through: HTTP, the CLI, Slack), the live or write probes it needs, and a pre-merge run with outcome and duration recorded. Only Sami's explicit scope change narrows, substitutes or defers it.
- A dataset, fixture or table the scenario names does not exist, or a probe matches zero records: mark the scenario `BLOCKED` and repair the plan against existing data. Do not drop or narrow it.
- Verification describes an outcome a user can observe. The reviewer rejects missing, proxy-only or internal-only verification.
- Track implemented, integrated and acceptance-verified separately: an unresolved dependency does not make a slice complete.

## Running it

- For a slice that changes what runs, run its affected end-to-end scenarios before merge, under the pre-merge scenario rule in `opening-a-pr` (`skill://opening-a-pr/references/proof.md`, "End-to-end scenarios", which also says which scenarios a change affects). Record the source, dependency and image revisions per scenario, then mark it with one of the markers (`RAN`, `BLOCKED`, `WAIVED-BY-SAMI`) that section defines. Its "Merge-ready" section says what to do when a surface cannot be reached or a blocker remains.
- Exercise every human entry point the plan's `## Contract change census` lists through the real client and the new check. Each entry not run is `BLOCKED`.
- Iterate on the repo's fastest loop and keep its slowest surface for the final proof. The repo's testing skill defines what counts as production-like.

## A slice with a browser page

Before handing over, the implementer runs ce-dogfood (read `~/.dotfiles/vendor/compound-engineering/skills/ce-dogfood/SKILL.md`) on its branch, with the real data the user sees and the user as the persona. It fixes clipped text, empty states and dead clicks. Its report goes on the slice's issue, and each decision it leaves for a person becomes a decision block in the spec.
