---
name: thermonuclear-deep-review
description: Use when asked for a thermonuclear or deep review, or for a security and correctness audit of a branch or PR diff. Covers bugs, breaking changes, security issues, developer-experience regressions and feature-gate leaks.
disable-model-invocation: true
---

# Thermonuclear Deep Review

As a security reviewer, audit the changes of the checked-out branch for bugs, regressions and security vulnerabilities. Be rigorous. Report only what the evidence supports.

## Scope

Report only issues in code that the PR adds or modifies. Exception: unchanged code that consumes behavior the diff changes (check 2). A broken consumer is a defect of this diff, but the diff never shows it.

## Checks

1. **Breakage.** Trace each change through its callers and contracts, across packages and modules.
2. **Consumers.** For each behavior the diff changes, name its consumers and show that each one still works. Behaviors: log level, status code, exception type, metric or field name, payload shape, identity or uniqueness key, timing, retry, default. Tracing callers does not find consumers, and many are outside the diff, the package or the repository:
   - alert and monitor predicates (a monitor on `status:error` stops when a line goes down to WARNING)
   - log-level contracts, structured-log field names, log-derived metric filters
   - status codes and error-type strings in request logs, webhooks and audit records
   - identity or uniqueness keys that a persistence layer arbitrates on (a new id for an existing row collides with a constraint)
   - dashboards, SLOs, saved queries, downstream parsers

   If you cannot find a consumer, say so. Never assume that none exists. A change that removes its own alarm is the highest severity here, because it also removes the signal that would catch it.
3. **Developer experience.** Find changes to how developers run or build the code locally. Examples: where or how the code reads secrets, new or renamed environment variables, remapped ports or networking, new scripts that a function needs. Not a break: a new alternative way to run or build, or a new package-manager dependency. Exception: a dependency that needs a step outside the usual workflow, such as a manual install from a website or app store, is a break.
4. **Feature leaks.** Trace each feature flag or internal-only check and its callers. Do not let a gated feature leak.
5. **Intended breakage.** An intended, well-constrained high-risk effect is not a defect. Report it when its scope or consequences are unclear, or when it removes a safeguard or feature gate.
6. **PR body claims.** Attack each safety or correctness claim in the PR body, not only the diff. Only a reviewer told to attack a claim finds a wrong one. Attack these first:
   - Order, not coverage: after a pipeline sanitizes, can any later pass recreate what the sanitizer neutralized?
   - A severity that relies on a third party's formatting is a dependency, not a mitigation. Rate it as that bet, and fix it, do not only disclose it.

## Security Guidelines

For each row whose surface the diff touches, answer with a file:line citation.

| Tag | If the diff touches… | Answer, with file:line |
| --- | --- | --- |
| `authz` | an authorization or refusal check, or a new route, command, or tool | who may call it, who may not, where the diff enforces that, and what the unauthorized caller gets |
| `secret` | a token, grant, secret file, credential helper, or its lifetime | what widened: who can read it, for how long, in which process |
| `untrusted-input` | a subprocess, argv, path, template, or query built from text an outside party controls (an issue body, a PR comment, a webhook payload, model output) | the boundary that neutralizes it, and whether any later pass can re-create what it neutralized (order, not coverage) |
| `prompt` | a prompt that embeds untrusted text into an agent's instructions | what delimits the untrusted region, and what the agent may do if it obeys that text (OWASP LLM01 prompt injection, LLM06 excessive agency) |
| `supply-chain` | a dependency, lockfile, base image, or GitHub Action | the version, the pin (a digest or a SHA checked against its tag), and the permissions the workflow runs with |
| `sandbox` | a sandbox or pod manifest, a capability, or a network policy | which isolation property changed, and against whom |

- Cite an OWASP ASVS v5.0.0 identifier where one applies (`v5.0.0-1.2.5` style).
- A finding with no stated exploit path is hardening: rank it Minor. A security finding with an exploit path is its own finding at its real priority, never part of hardening.
- Report at most two hardening items, ranked. Put the rest in one hardening paragraph.
- Start each security finding with its row's tag, `Security[<tag>]:`, so readers can count findings by row.
- End the report with one `Security:` line: each touched row's tag and its answer with file:line, or `Security: no sensitive surface in this diff.` when the diff touches none.

## Findings

- Never misreport priority. Developers who get false High findings stop listening. Trace each issue end to end.
- Never report a finding with research still open. If you can read the code that settles it, read it. Not: "the client has issue X, but if the backend handles it, this is ok."

## Verdict

Make the first line of the report the verdict, in exactly this form, with the short sha of the head commit you reviewed (`git rev-parse --short=8 HEAD`):

- `Verdict: CHANGES at <sha>` when the report has a finding that the author must fix before merge.
- `Verdict: APPROVE at <sha>` when it has none. Hardening items alone do not block.

`pr-gates` reads only the first line of each PR comment, so put nothing before the verdict.

## PR discussion

Only after your independent audit, and only when you have Medium-to-High findings and the branch has a PR:

1. Read the PR/MR discussion with `gh` or `glab` for comments from BugBot or others.
2. Check their findings. Include the valid ones you missed, and add what is useful from the ones you share.
3. Mark each finding that came from that discussion.
