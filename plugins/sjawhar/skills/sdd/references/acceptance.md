# Acceptance detail

## The scenario in every brief

Every worker brief carries its slice's full acceptance scenario plus the decisions and rejections
for its step, with sources.

- The scenario covers existing data, the real user and transport (the client the user reaches the change through: HTTP, the CLI, Slack), required live or write probes, and a pre-merge run with outcome and duration recorded.
- Only Sami's explicit scope change narrows, substitutes or defers it.
- The dataset, fixture or table the scenario names does not exist, or a probe matches zero records: mark the scenario `BLOCKED` and repair the plan against existing data. Never drop or narrow it.
- Verification describes a user-observable outcome. The reviewer rejects missing, proxy-only or internal-only verification.
- Track **implemented**, **integrated** and **acceptance-verified** separately: an unresolved dependency does not establish completion.

## Running it

- For a slice that changes what runs, run its affected end-to-end scenarios before merge, under the pre-merge scenario rule in `opening-a-pr`.
- Record source, dependency and image revisions per scenario, then mark it:
  - `RAN`: observed through the real surface (the command, URL or record a user touches, not a test, a fixture or a log line), with outcome and duration.
  - `BLOCKED`: the failed command and the authoritative record that shows the blocker (the CI run, issue or record, linked), never a person's name. Check another session's reported blocker against that record.
  - `WAIVED-BY-SAMI`: a named blocker he explicitly waived, linked to his words. It covers that blocker on that change only.
- For the plan's `## Contract change census`: exercise every human entry point it lists through the real client and the new check. Each unrun entry is `BLOCKED`.
- Build or repair missing infrastructure, tooling, skills or drivers at the tested head, then rerun the affected scenarios.
- Never ask Sami to omit acceptance or to accept a proxy. A named blocker that survives building or repairing the surface goes to him as a pre-merge decision. His explicit waiver of that blocker is the only exception; the change's size, its kind and green CI never are.
- Iterate on the repo's fastest loop and keep its slowest surface for final proof, not for each edit. The repo's testing skill defines the rungs and what counts as production-like.

## A slice with a browser page

Before handing over, the implementer runs ce-dogfood (read `~/.dotfiles/vendor/compound-engineering/skills/ce-dogfood/SKILL.md`) on its branch, with the real data the user sees and the user as the persona. It fixes clipped text, empty states and dead clicks; its report goes on the slice's Dispatch issue, and each decision it leaves for a person becomes a decision block in the spec (`references/plan-sections.md`).
