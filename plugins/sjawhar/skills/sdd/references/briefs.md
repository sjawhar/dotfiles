# Worker briefs

Each worker brief is a role prompt plus the task. At dispatch time fetch each part from
`https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/`,
never from a local copy: a local copy drifts from the prompts Legion reviews.

| Purpose | Agent | Parts, in order |
|---|---|---|
| implement or debug | `deep` | `core/common.md`, `core/implementer.md`, `mechanics/interactive.md` |
| acceptance | `deep` | `core/common.md`, `core/tester.md`, `mechanics/interactive.md` |
| review | `reviewer` | `core/common.md`, `core/reviewer.md`, `mechanics/interactive.md` |
| research | `oracle` | `core/oracle.md`, `mechanics/interactive.md` |

Do not fetch `core/planner.md`: here you write the plan. Reject non-200 responses and empty bodies:

```bash
fetch_part() {
  url="$1"; out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$url") || { echo "role prompt unavailable: $url (curl: $code)" >&2; return 1; }
  [ "$code" = "200" ] || { echo "role prompt unavailable: $url (HTTP $code)" >&2; return 1; }
  [ -s "$out" ] || { echo "role prompt unavailable: $url (HTTP 200, empty body)" >&2; return 1; }
  cat "$out"
}
```

- Join the parts and a `# Task` section with blank lines. The task names the absolute workspace, the step brief, the acceptance criteria, the one branch or jj bookmark the worker may push, the absolute path it writes its report to, and the skill-catalog entries.
- Keep fetched text verbatim. A failed fetch blocks the dispatch, with no fallback. Change the prompts only through review in Legion.
- Check commands in a brief are CI's exact recipe from the repo guide or workflow, not a subset: a subset passes where CI fails.

## While the workers run

- Dispatch independent work concurrently. Take results as events, never by polling, and keep unblocked work moving.
- A silent worker blocks you: message it once with a direct question, and redispatch the same brief only if it still does not answer.
- A `deep` worker whose bash tool breaks gets the kernel-hosted shell from `using-subagents`; it has no eval tool.
- While a PR waits for merge or deploy, continue from its branch or a dependency's branch, unless you are revising it. Post-merge production proof runs alongside the next work, not instead of it.
