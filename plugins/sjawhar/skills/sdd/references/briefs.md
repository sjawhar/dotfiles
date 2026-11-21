# Worker briefs

Each brief is Legion's role prompt parts, then a `# Task` section, joined with blank lines. Fetch the parts at dispatch time from `https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/`, never from a local copy, which drifts from the prompts Legion reviews:

- implement or debug (`deep`): `core/common.md`, `core/implementer.md`, `mechanics/interactive.md`
- acceptance (`deep`): `core/common.md`, `core/tester.md`, `mechanics/interactive.md`
- review (`reviewer`): `core/common.md`, `core/reviewer.md`, `mechanics/interactive.md`
- research (`oracle`): `core/oracle.md`, `mechanics/interactive.md`

Do not fetch `core/planner.md`: you write the plan. Reject a non-200 response or an empty body:

```bash
fetch_part() {
  url="$1"; out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$url") || { echo "role prompt unavailable: $url (curl: $code)" >&2; return 1; }
  [ "$code" = "200" ] || { echo "role prompt unavailable: $url (HTTP $code)" >&2; return 1; }
  [ -s "$out" ] || { echo "role prompt unavailable: $url (HTTP 200, empty body)" >&2; return 1; }
  cat "$out"
}
```

- Keep fetched text verbatim. A failed fetch blocks the dispatch, with no fallback. Change the prompts only through review in Legion.
- The task names the absolute workspace, the step brief, the acceptance criteria, the one branch or jj bookmark the worker may push, the absolute path of its report, and its skill-catalog entries.
- Check commands in a brief are CI's exact recipe from the repo guide or workflow: a subset passes where CI fails.

## While the workers run

- When a worker looks silent, follow `using-subagents`' `references/waiting.md` ("A lane that looks stalled").
- A `deep` worker has no eval tool: when its bash breaks, give it a shell tool from your kernel (`using-subagents`).
- While a PR waits for merge or deploy, continue from its branch or a dependency's branch, unless you are revising it. Post-merge production proof runs beside the next work, not instead of it.
