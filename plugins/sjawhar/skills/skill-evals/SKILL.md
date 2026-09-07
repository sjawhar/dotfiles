---
name: skill-evals
description: "Use when changing WATCHDOG.md, advisor prompts, agent instructions, skills, or MCP/tool feedback and needing evidence from frozen synthetic or privately reviewed cases before adoption."
---

# Agent Evals

Use the installed `agent-evals` CLI to measure a proposed agent-facing change
against frozen cases before adopting it. It covers activation, effects, advisor,
and tool-feedback behavior. Freeze cases, variants, and grading criteria before
model calls; an eval result is evidence about the evaluated versions only.

The private code-and-data root defaults to `$HOME/.agent-eval`. Always set an
explicit dataset root and pass explicit case, variant, and run paths. Do not use a
dotfiles source checkout or `uv run --project evals`.

```bash
: "${AGENT_EVAL_ROOT:=$HOME/.agent-eval}"
agent-evals --version
agent-evals validate --cases "$AGENT_EVAL_ROOT/cases"
```

## Select the right surface

| Change | Track | Required evidence |
|---|---|---|
| Skill loading, watchdog, routing | `activation` | Synthetic paired baseline/candidate run |
| Prompt, instruction, or simulated action outcome | `effects` | Synthetic paired run; use isolation evidence when sandboxed |
| Advisor policy or intervention timing | `advisor` | Reviewed cases and paired burden/veto report |
| MCP/tool selection, recovery, or feedback | `tools` | Tool-gateway paired run with actual transport |

Use the narrowest frozen case set that covers the changed observable behavior.
Do not run a broad corpus merely because it exists.

## Run and compare

Create a fresh run directory under the explicit root. The runner creates a
contained `logs/` directory and a frozen manifest before provider calls. It
records raw Inspect attempt identifiers and errors; comparison keeps failed and
unsupported trials visible.

```bash
RUN_DIR="$AGENT_EVAL_ROOT/runs/activation-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$RUN_DIR" || { echo "choose a fresh run directory" >&2; exit 1; }

secrets ANTHROPIC_API_KEY -- agent-evals run \
  --cases "$AGENT_EVAL_ROOT/fixtures/activation/cases.jsonl" \
  --variants "$AGENT_EVAL_ROOT/fixtures/activation/variants.json" \
  --model anthropic/claude-opus-4-6 \
  --run-dir "$RUN_DIR" --track activation --repeats 3
agent-evals compare --run-dir "$RUN_DIR"
```

`compare` resolves saved evidence from the supplied run directory. It never
requires the historical absolute log path to still exist. Inspect native evidence
with `inspect view --log-dir "$RUN_DIR/logs"`.

## Imported history and label review

Historical archives remain raw records. Import produces pending cases and an
admission ledger without a model call. Structural source, digest, MIME, and
artifact-containment failures stay in that ledger. OMP bridge traces retain
key-only redaction for newly injected transport credentials; that does not change
historical archive payloads. Do not erase or rewrite source recordings, labels,
feedback, or existing review statuses.

A case remains non-executable until its ordinary label review is approved.
Existing privacy-review fields are historical provenance only; they neither block
import nor authorize execution. Never auto-promote a pending case.

```bash
DATA_DIR="$AGENT_EVAL_ROOT/verification/import-$(date -u +%Y%m%dT%H%M%SZ)"
agent-evals import \
  --legacy "$AGENT_EVAL_ROOT/fixtures/import/legacy" \
  --sources "$AGENT_EVAL_ROOT/fixtures/import/sources.json" \
  --data-dir "$DATA_DIR"
! agent-evals validate --cases "$DATA_DIR/cases"

agent-evals promote \
  --case-id synthetic-regression \
  --cases "$DATA_DIR/cases" \
  --decision "$AGENT_EVAL_ROOT/fixtures/import/review-decision.json"
agent-evals validate --cases "$DATA_DIR/regressions"
```

A review decision supplies `case_id`, `decision=approved`, reviewer, review time,
rationale, required output, and the track's reviewed target. It records a
versioned regression case; it does not modify the source pending case.

## Interpret the result

Report the case/variant/version set, run directory, exact metric deltas, sample
counts, error/unsupported counts, and veto findings. Inspect meaningful outliers
before recommending adoption. Do not treat a failed provider call, grader failure,
or missing paired observation as a favorable score.
