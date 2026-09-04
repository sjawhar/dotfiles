---
name: skill-evals
description: "Use when changing WATCHDOG.md, advisor prompts, agent instructions, skills, or MCP/tool feedback and needing evidence from frozen synthetic or privately reviewed cases before adoption."
---

# Agent Evals

Use the Inspect-backed `agent-evals` suite to determine whether a proposed
agent-facing change improves behavior without creating a regression. It covers:

- **activation** — whether the right skill is loaded at the right time;
- **effects** — whether a skill, prompt, or instruction changes the resulting
  response, simulated state, or isolated sandbox state;
- **advisor** — whether advice is timely, grounded, and safe; and
- **tools** — whether agents choose, repair, and respect MCP/tool boundaries.

Freeze the cases, variants, and grading criteria before model calls. A result is
evidence about the evaluated versions only; it never deploys a candidate.

## Case storage and historical data

Committed fixtures are synthetic only. Historical recordings, labels, expected
answers, prompt snapshots, logs, and run output belong in approved private
versioned storage outside this repository.

Before importing actual history, an existing approved private repository root
must contain `.agent-evals-destination.json` and the declared version-control
metadata:

```json
{
  "schema_version": 1,
  "repository_visibility": "private",
  "verified_by": "reviewer name",
  "verified_at": "2026-09-06T00:00:00Z",
  "version_control": "jj",
  "revision": "verified private revision"
}
```

Select `<data-dir>` as a fresh, non-symlinked child beneath that root. The
import guard validates the nearest lexical VCS boundary, retains that exact
canonical child as its output location, and rejects a symlinked destination or
an attempt to cross a nearer nested VCS boundary. Import and promotion share
the same storage guard as `validate` and `run`: every public dotfiles checkout
is rejected, and recorded cases must remain below the attested private root.
The digest-verified bundled synthetic fixtures are the only exception; their
exact model-visible replay digests are checked. The commands do not create
repositories, configure remotes, or move source sessions. The existing legacy
corpus remains untouched until copied source hashes and a durable private
revision have been verified.

Imported corpus output is an indexed collection, not a generated monolithic
JSONL file: `<data-dir>/cases/index.json` references immutable digest-named
shards and any content-addressed blobs in `<data-dir>/cases/artifacts/`.
Promotion appends a reviewed case to the ordered
`<data-dir>/regressions/` collection and atomically republishes its index.
Every artifact block is an ID, MIME type, and SHA-256 pointer whose matching
record lives in `environment.artifacts`; source bytes are never embedded in
the replay transcript. Artifact-bearing imports, validation, promotion, and
runs require an attested private ancestor even for synthetic data. The
text-only fixture exception does not extend to artifacts.

The controller-provided, unversioned `sources.local.json` requires explicit
read-only source roots. Roots may be absolute or `~`-expanded on the controller
machine, or safe relative paths resolved from that configuration file. A root
that resolves to its filesystem anchor is rejected before discovery. Every
chosen source is canonicalized and must remain inside its declared non-root
source root, so a symlink or recursive discovery path cannot escape it. Raw
source paths and session copies are never versioned.

The local config may reference a private, versioned logical identity/SHA map
only through the strict relative, contained `omp_legacy_prefixes_file` path:

```json
{
  "omp_roots": ["recordings/omp"],
  "opencode_roots": ["recordings/opencode"],
  "claude_roots": ["recordings/claude"],
  "omp_legacy_prefixes_file": "omp-legacy-prefixes.json"
}
```

`omp-legacy-prefixes.json` is private migration evidence, not a filename
search:

```json
{
  "2026-01-02T10-00-00-": {
    "full_session_id": "2026-01-02T10-00-00-123Z_full-id",
    "source_sha256": "lowercase sha256 of that exact source file"
  }
}
```

The importer first resolves an exact source session filename. Only an exact
20-character OMP timestamp-prefix key may then use this private map; its mapped
full ID must extend the prefix, resolve to exactly one source file, and match
the recorded SHA-256. It never runs a substring search. A missing or malformed
map, mismatched full ID, ambiguous file, or digest mismatch is ledgered rather
than admitted.

The intervention text and supplied timestamp must select one event exactly.
Only when `intervention_ts` is absent or `null` may the importer recover it:
after verified session resolution, the complete normalized `sami_message` must
match exactly one user event. It records that event's ID, timestamp, and
`unique_normalized_user_text` recovery method in private provenance. Empty,
malformed, or contradictory supplied timestamps, zero/multiple text matches,
and nearest/time-only/cross-session matching remain rejections.

The importer preserves ordered text, thinking, tool calls, and tool results. It
uses the source-defined OMP rendering and output notices for visible
`bashExecution` messages, omits executions excluded from the original model
context, and preserves an OpenCode compaction boundary as an empty marker; it
never invents a resume prompt. Text-only `fileMention` material remains
developer text. For a mixed file mention, its text and image groups remain
separate developer and user events with deterministic IDs and
`provenance.event_origins` back to the source record. Supported self-contained
OMP and Claude base64 images, plus OpenCode data-URI file parts for UTF-8 text,
JSON, PNG, JPEG, WebP, and GIF, become hash-bound artifacts in original block
order; they are not flattened to text or treated as inspected pixels. External
OpenCode paths or URLs and non-base64 Claude image sources are rejected rather
than fetching current content. Retained text and JSON artifact bytes receive
the same credential and source-root redaction before storage, and their block
and manifest digests bind the resulting bytes. Image artifacts are not
text-scanned or treated as inspected pixels; they remain pending human privacy
review. Encrypted/provider-internal blocks, unsupported media, malformed
artifact data, and nonempty diagnostic metadata remain ledgered rather than
being silently downgraded.
The importer excludes the later correction and expected label from
the subject event window; it redacts recognized credential shapes and each
declared canonical source-root form from retained text, tool arguments, JSON
keys, and ledger errors. A redaction-induced JSON-key collision rejects the
candidate rather than overwriting a recorded value. It writes one
admission-ledger outcome for every candidate. Malformed JSONL lines and
secret-bearing candidate identifiers are ledgered as `line-N`; the latter are
quarantined before an identifier can be redacted into a collision.
Structurally unresolved findings such as private-key blocks are quarantined
before storage. This is not exhaustive secret detection: pending human privacy
review remains mandatory for residual sensitive context. Imported labels and
`provenance.privacy_review` are always `pending`; they are data-only and
non-executable.

## Synthetic import and review loop

Run the complete import path on the bundled digest-verified synthetic source
root before changing corpus behavior:

```bash
DATA_DIR="$(mktemp -d)"
uv run --project evals agent-evals import \
  --legacy evals/fixtures/import/legacy \
  --sources evals/fixtures/import/sources.json \
  --data-dir "$DATA_DIR"
# Recorded imports are data-only. This executable-admission check must fail while
# their label/privacy review is pending.
! uv run --project evals agent-evals validate --cases "$DATA_DIR/cases"
uv run --project evals agent-evals promote \
  --case-id synthetic-regression \
  --cases "$DATA_DIR/cases" \
  --decision evals/fixtures/import/review-decision.json
# Explicitly reviewed and privacy-approved regressions validate and may run.
uv run --project evals agent-evals validate \
  --cases "$DATA_DIR/regressions"
```

The digest-verified synthetic exception applies only to fixture source import.
It never authorizes an unattested `--run-dir` or comparison output.

Inspect `admission-ledger.jsonl` after every import. A rejected locator, missing
source session, unsupported content block, secret quarantine, or schema error
is evidence to resolve rather than a case to silently drop.

Promotion needs a recorded human label and privacy decision:

```json
{
  "case_id": "case-id",
  "decision": "approved",
  "reviewer": "reviewer name",
  "reviewed_at": "2026-09-06T00:00:00Z",
  "rationale": "Why the label and regression are correct.",
  "required_output": "observable requirement",
  "privacy_reviewer": "privacy reviewer name",
  "privacy_reviewed_at": "2026-09-06T00:00:00Z",
  "reviewed_target": {
    "should_advise": true,
    "severity": "concern",
    "criterion": "human-reviewed-criterion",
    "prior_advice_criteria": []
  }
}
```

The recorded source provenance stays with the promoted case. `validate` and
the runner apply the same executable-admission gate: they reject every recorded
case until `review_status` is `approved`,
`provenance.privacy_review` is exactly `{"status":"approved","reviewer":"…","reviewed_at":"…"}`,
and the cases path is in external private storage; they never treat historical
recordings as synthetic. Importing remains a data-only operation and does not
authorize inference.

Promotion leaves the pending source case intact and creates a numbered,
provenance-linked regression copy. The track-specific `reviewed_target` is a
human decision, never a model inference: for Advisor it must supply exactly
`should_advise`, `severity`, `criterion`, and `prior_advice_criteria`. Run that
saved regression through the relevant candidate evaluation after every related
change; a later deliberately bad candidate must fail before the change can be
adopted.

## Evaluating a candidate

Prepare one baseline and one or more candidate variants. The baseline has
`role: "baseline"` and `baseline_id: null`; each candidate has
`role: "candidate"` and names that exact baseline ID. Run the frozen comparison
with the acceptance model:

```bash
# PRIVATE_DATA_DIR must name a fresh, non-symlinked child below an existing
# attested private version-controlled corpus root; it is not its own repository.
: "${PRIVATE_DATA_DIR:?Set PRIVATE_DATA_DIR to a fresh child of the approved private corpus root}"
CASES_PATH="$PRIVATE_DATA_DIR/regressions"
VARIANTS_PATH="$PRIVATE_DATA_DIR/variants.json"
TRACK="advisor"
REPEATS=3
# `agent-evals run` requires a new path below that selected private child.
PRIVATE_RUN_DIR="$PRIVATE_DATA_DIR/runs/$(date -u +%Y%m%dT%H%M%SZ)-advisor"
test ! -e "$PRIVATE_RUN_DIR" || {
  printf 'run directory already exists: %s\n' "$PRIVATE_RUN_DIR" >&2
  exit 1
}

secrets ANTHROPIC_API_KEY -- uv run --project evals agent-evals run \
  --cases "$CASES_PATH" \
  --variants "$VARIANTS_PATH" \
  --model anthropic/claude-opus-4-6 \
  --run-dir "$PRIVATE_RUN_DIR" \
  --track "$TRACK" \
  --repeats "$REPEATS"
uv run --project evals agent-evals compare --run-dir "$PRIVATE_RUN_DIR"
```

Use the track and rollout tier that prove the proposed claim. One-shot tests
observe a response, simulated tests observe validated simulator state, and
sandbox tests observe isolated Docker state. Advisor false alerts count as
three missed-nit units; harmful advice and serious misses are separate failures.
Tool and sandbox execution never run against the host, user credentials, live
services, or user memory.

Use Inspect to view private run logs:

```bash
uv run --project evals inspect view \
  --log-dir "$PRIVATE_RUN_DIR/logs" \
  --host 127.0.0.1 \
  --port 7575
```

## Historical migration gate

Once a private destination is approved, inventory every legacy candidate,
import the complete population, reconcile admission-ledger counts to the
inventory, verify source/stored hashes and the private revision, then review
labels. Do not run model evaluations on historical cases until privacy and
label review are complete.
