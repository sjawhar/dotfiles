# Agent Evaluation Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development with Sami's mapping: Main plans and integrates; `deep` implements/debugs; `reviewer` gates plans and code; `oracle` is read-only strategy support. Do not delegate further from a worker.

**Goal:** Deliver one operator-facing Inspect suite for skill activation, instruction effects, advisor behavior and MCP/tool feedback, including simulated and real sandbox execution and durable historical-case migration.

**Architecture:** A small Python package in `evals/` owns validated cases, safe run configuration, four Inspect task factories and paired reports. Independent factories are implemented concurrently against the shared contract below; the core owner supplies packaging and CLI integration. The effects owner supplies the real OMP sandbox adapter; other tracks may complete pure-model work independently of it.

**Tech Stack:** Python 3.12+, Inspect AI 0.3.263 (verified available through `uvx`), Pydantic 2, pytest, MCP SDK, Docker/Compose. Use Inspect's bootstrap/CI facilities and standard scientific Python where needed; no promptfoo/evalstats/optimizer dependency. The installed OMP release is `18.1.7-sami.20260903-193013`; sandbox builds must pin a verified release asset, not invoke the user's shim.

**Spec:** `docs/plans/2026-09-06-agent-evaluation-design.md` (concise human-facing design).

## Global Constraints

- The short spec is authoritative. The work includes all four tracks and all three effect tiers; no reduced rollout or mock-only delivery.
- This is public dotfiles. Real cases, expected answers, private prompt snapshots, logs and temporary run output live outside the repo in private storage. Synthetic fixtures alone may be committed here.
- Historical data lives in the approved private `sjawhar/agent-eval-data` repository. The complete 856-candidate import is privately versioned and byte-verified; all retained cases remain pending label/privacy review and cannot be sent to models.
- Only the named workspace is writable: `$DOTFILES_DIR/.superpowers/worktrees/agent-evaluation`. Do not modify live OMP/OpenCode config, credentials, memory, other workspaces or vendor code. The primary checkout's legacy data is read-only until a verified migration is explicitly performed.
- Use jj, never git commands. Workers do not commit/rebase/bookmark/push; Main snapshots/reviews the single integration workspace to avoid shared-operation races. No project-wide format/lint/tests during concurrent mutation; write tests and run the actual assigned operator surface once dependencies are ready. Main runs combined checks after integration.
- Use strict typed models, unknown fields rejected at admission, explicit missing/error states, ordered original message blocks and call/result linkage. A missing transcript is not an empty transcript; lack of a user correction is not a negative label.
- Baseline, candidate, cases and scoring criteria are fixed before execution. No cached stochastic answers counted as repeats; no retry-best selection; do not pass labels, future corrections or another trial's output to the subject or simulator.
- False advisor alerts cost three missed-nit units. Serious misses and harmful remedies are separate failures; correct detection does not forgive harmful advice. No claimed mathematical equivalence to F-beta.
- Executable agents, raw tools, MCP servers and bypass probes execute via Inspect's Docker sandbox, never its `local` backend or host subprocess tools. Container state is reset per trial; no user home, credential store, host sockets or metadata-service access. Host-resident pure simulation may only process data, not execute model-controlled code.
- Provider tokens stay in the controller. The pinned subject uses Inspect's sandbox agent bridge; provider-side network tools and arbitrary host tool forwarding are disabled. Explicitly configure generation forwarding and record effective requests. `--no-session`/`--no-rules`/`autoRetain` are not isolation proofs.
- Existing watchdog direct-model-call isolation is reusable. Do not run the real primary-agent runtime for cheap transcript-only tests. The old runner's free-text imitation of `advise` and its negative-label assumptions are not preserved as valid scoring.
- Agent inference used for acceptance: `anthropic/claude-opus-4-6`, verified in the authenticated model list. It is not a cheap/smol tier. Invoke through `secrets ANTHROPIC_API_KEY -- ...`; never print token values. Defaults must not silently select another model.
- Shortcuts are permitted only if logged immediately in the Hardening ledger and returned in the report. Every unpaid entry is fixed before shipping. A worker's completion requires user-surface evidence, not just a green checker.

## Shared interfaces and file ownership

All workers use `from agent_evals.contracts import EvalCase, Variant, RunConfig`. Core owns these definitions. The following is the binding model contract (Pydantic models with `ConfigDict(extra="forbid")`; implementation can add validated fields by coordinator ruling, never silently rename them):

```python
class SkillSpec(BaseModel):
    name: str
    description: str
    body: str

class TextBlock(BaseModel):
    type: Literal['text'] = 'text'
    text: str

class ThinkingBlock(BaseModel):
    type: Literal['thinking'] = 'thinking'
    text: str

class ToolCallBlock(BaseModel):
    type: Literal['tool_call'] = 'tool_call'
    id: str
    name: str
    arguments: dict[str, JsonValue]

class ArtifactBlock(BaseModel):
    type: Literal['artifact'] = 'artifact'
    artifact_id: str
    media_type: str
    sha256: str

ContentBlock = Annotated[TextBlock | ThinkingBlock | ToolCallBlock | ArtifactBlock,
                         Field(discriminator='type')]

class Event(BaseModel):
    id: str
    role: Literal['system', 'developer', 'user', 'assistant', 'tool']
    content: list[ContentBlock]
    tool_call_id: str | None = None
    source_role: str | None = None
    truncated: bool = False
    tool_name: str | None = None
    is_error: bool = False

class EvalCase(BaseModel):
    id: str
    family_id: str
    track: Literal['advisor', 'activation', 'effects', 'tools']
    tier: Literal['one_shot', 'simulated', 'sandbox']
    prompt: str
    events: list[Event] = []
    unavailable_tool_call_ids: list[str] = []
    skills: list[SkillSpec] = []
    criteria: list[str]
    target: dict[str, JsonValue]
    environment: dict[str, JsonValue] = {}
    provenance: dict[str, JsonValue] = {}
    review_status: Literal['synthetic', 'pending', 'approved', 'rejected']
    split: Literal['development', 'calibration', 'test', 'regression']

class Variant(BaseModel):
    id: str
    role: Literal['baseline', 'candidate']
    baseline_id: str | None = None
    agent_prompt: str = ''
    context_files: dict[str, str] = {}
    skill_overrides: dict[str, SkillSpec] = {}
    omit_skills: list[str] = []
    advisor_prompt: str = ''
    skill_loading: Literal['catalog', 'preloaded'] = 'catalog'
    tool_mode: Literal['raw', 'wrapped', 'guarded'] = 'raw'

class RunConfig(BaseModel):
    model: str
    judge_model: str
    simulator_model: str
    feedback_model: str
    repeats: int = 3
    max_connections: int = 4
    max_sandboxes: int = 2
    token_limit: int = 16000
    time_limit: int = 300
    log_dir: Path

class OperationEvent(BaseModel):
    operation_id: str
    phase: Literal['queued', 'started', 'committed', 'cancelled']
    timestamp: str

class DeliveryReceipt(BaseModel):
    operation_id: str
    channel: Literal['aside', 'steer', 'preserve']
    delivered_at: str
    runtime_event_id: str
    phase_at_delivery: Literal['queued', 'started', 'committed', 'cancelled']

class OmpRunResult(BaseModel):
    output: str
    events: list[Event]
    requests: list[dict[str, JsonValue]]
    state: dict[str, JsonValue]
    operation_events: list[OperationEvent]
    advice: list[dict[str, JsonValue]]
```

All block models also forbid extra fields. Use `Field(default_factory=...)` for collections. A tool-result message is `Event(role='tool', tool_call_id=CALL_ID, tool_name=NAME, content=[TextBlock(...)], is_error=...)`; it must reference a preceding unique ToolCallBlock.id, unless the case explicitly records an omitted earlier call as unavailable context. Normalize source aliases (`toolCall`, `tool_use`, etc.) at import only. Nested Claude tool_result blocks become ordered tool events with source-id suffixes. Unknown content types are rejected or retained as referenced opaque artifacts with an explicit unsupported-replay outcome, never silently dropped. Targets/provenance/review state belong to scorer/controller metadata, never subject messages, simulator prompts or model-generated feedback. Case files are JSONL. Variant/run files are JSON; relative paths resolve against their document before boundary checks.

`criteria` is the hidden grading rubric and is controller/grader-only, alongside target/provenance/review state. Case-to-Sample conversion must allowlist subject fields rather than serialize EvalCase wholesale. Test a unique criterion canary is absent from subject, simulator and feedback requests. Legitimate task requirements belong in the public `prompt`, not solely in hidden criteria.

A variants file is a JSON list containing exactly one `role='baseline'` entry (`baseline_id=null`) and one or more candidates pointing to its exact ID. Reject duplicate IDs, absent/wrong baseline references, or a candidate marked as its own baseline before model calls. Preserve this pairing in the run manifest; compare each candidate independently with the shared baseline. Tool fixtures therefore express raw baseline plus wrapped/guarded candidates without positional naming assumptions.

Each track exports exactly:

```python
def build_task(cases: list[EvalCase], variant: Variant, config: RunConfig) -> inspect_ai.Task:
    ...
```

The factory rejects wrong-track inputs. It records case/family/variant/tier in Inspect metadata and supplies named scorer values. Core lazily imports the selected track; no empty sibling stubs. `Score.value` contains only scalar numeric/bool/string values supported by Inspect, including `success` (0/1 or unscored). Put status, structured criterion results, runtime kind and detailed evidence in `Score.metadata`/explanation, not nested dictionaries in the numeric metric. Exceptions remain sample errors and appear in the report. Core reports success plus the track's declared scalar metrics and never assumes every metadata field is numeric.

Shared operator commands (core owns argparse and package entry points; workers own leaf implementation):

- `agent-evals run --cases FILE --variants FILE --model MODEL --run-dir DIR [--judge-model MODEL] [--simulator-model MODEL] [--feedback-model MODEL] [--track NAME] [--repeats N]`: each omitted role explicitly defaults to `--model`; print and freeze all four resolved models before execution. Judge model grades, simulator model generates tool results, feedback model generates MCP corrective feedback. No hidden fallback model. Validate/freeze config+hashes, run variants through Inspect, print run directory/log locations. No default output inside cwd.
- `agent-evals compare --run-dir DIR`: print/write per-track/per-case baseline/candidate changes, intervals, errors and evidence-log references, including 3:1 advisor burden. No forced verdict when evidence is insufficient.
- `agent-evals import --legacy DIR --sources FILE --data-dir DIR`: read legacy inputs, locate source sessions using explicit roots in sources.json, rebuild events, write pending cases + full admission ledger. Private destination is required for actual history.
- `agent-evals promote --case-id ID --cases FILE --decision FILE`: validate a recorded human adoption/label decision, create a versioned regression entry without destroying the source case.
- `agent-evals validate --cases FILE`: expose actionable admission/schema errors without model calls.
- `agent-evals isolation-check --run-dir DIR`: core validates output location then calls Task 4's `adapters.omp.run_isolation_check`; no provider/model call is required. Write observed canary/socket/network results into the run directory and exit nonzero if a prohibited path succeeds.

Leaf CLIs are not invented separately. Inspection/viewing uses `uv run --project evals inspect view --log-dir RUN_DIR --host 127.0.0.1 --port 7575`; Main starts any persistent viewer via `hub`. Import and promotion implementations are owned by the corpus worker and registered by core after both are ready.

Expected track metrics:

- advisor: `detected`, `advice_valid`, `false_alerts`, `missed_nits`, `serious_misses`, `duplicate_notes`, `harmful_advice`; separately delivery/side-effect results for sandbox integration;
- activation: `should_load`, `loaded`, `timely`, `wrong_loads`, `load_errors`;
- effects scalar values: `success`, `simulator_errors`; metadata: `criterion_results`, `evidence_kind` (`response`, `simulated_state`, `sandbox_state`), `state_verified` (null/not applicable for one-shot), `runtime_kind`, successful skill-body identity/hash;
- tools: `success`, `selected_wrapper`, `request_repaired`, `rejected_valid_request`, `bypass_attempted`, `bypass_succeeded`, `state_verified`.

## Execution graph

Dispatch Tasks 1–6 in one batch after plan approval. Task 1's core interfaces/package are a real integration dependency, not a reason to delay writing independent leaf modules. Each worker can build its own fixtures/tests while core lands. Task 4 owns the shared real-OMP adapter; Task 2's live advisor delivery check consumes it after it is available. Core and corpus cooperate only at CLI registration and private-migration cutover. Task 7 is the joined acceptance/review phase.

| Task | Exclusive ownership |
|---|---|
| 1 Core | `evals/pyproject.toml`, `uv.lock`, `.gitignore`, `src/agent_evals/{__init__,__main__,contracts,runner,comparison}.py`, core tests, `evals/README.md` |
| 2 Advisor | `tracks/advisor.py`, `prompts/advisor_*.md`, advisor fixtures/tests, `adapters/advisor_delivery.py` |
| 3 Activation | `tracks/activation.py`, activation fixtures/tests |
| 4 Effects + OMP | `tracks/effects.py`, `adapters/omp.py`, `sandbox/`, `prompts/simulator*.md`, effect fixtures/tests |
| 5 Tools | `tracks/tools.py`, package-owned `tool_gateway/` runtime, `fixtures/tool_gateway/{cases.jsonl,variants.json}`, tool prompts/tests |
| 6 Corpus/cutover | `corpus.py`, import/promotion tests+synthetic source fixtures, `plugins/sjawhar/skills/skill-evals/SKILL.md`; old skill-evals scripts deleted only after replacement integration |

Shared directory creation is allowed; shared file editing is not. Root exports remain core-owned. Each worker sends a short readiness message when its public function becomes usable. Main decides any interface change and sends it to all affected workers before they edit callers.

## Task 1: Shared case contracts, safe CLI and comparison report

**Files:** ownership table Task 1, plus `evals/tests/test_{contracts,runner,comparison}.py`.
**Consumes:** the fixed protocol above and sibling `build_task` functions.
**Produces:** installable package, all operator commands, safe run manifest/logging, paired report.

- [ ] Write contract tests covering duplicate case IDs, missing tool result IDs, wrong labels, output path under public root (including symlink), zero-denominator metrics and unmatched baseline/candidate trials.
- [ ] Build the package with Inspect pinned to 0.3.263 and project-local dependencies/lock. Add `!/evals/uv.lock` and narrow output-ignore backstops; do not change the global `uv.lock` rule for unrelated projects.
- [ ] Implement immutable run initialization before Inspect logging/model calls. Canonicalize/reject public output paths; use `INSPECT_LOG_DIR` and explicit `log_dir`. Preserve raw attempt IDs, errors, runtime/config hashes and paired variant association. Disable stochastic output caching and whole-trial retries by default. Enforce configured connection/sandbox/token/time limits through Inspect; all subject/grader/simulator calls must use its model APIs.
- [ ] Implement case-to-Sample conversion that keeps private targets out of input, and lazy track loading. Export only the documented models/functions; no compatibility aliases.
- [ ] Use native `eval`/`eval_set` and .eval logs. Align reports on case/family/variant/repeat; bootstrap at family level retaining repeated trials; report unsupported/errored comparisons. Advisor burden is `3 * false_alerts + missed_nits` per decision window. `false_alerts` includes unsupported and unnecessary duplicate alerts; no extra field named unnecessary_alerts exists. Harmful advice/serious misses are independent veto flags. No F-beta substitution.
- [ ] Provide `compare` and an Inspect-viewer workflow with concrete failure examples and source log references. Add the short operator README covering setup, a synthetic run, private import and regression promotion.
- [ ] Verification outcome: the actual `agent-evals run` command refuses an in-repo run directory before a model call, writes valid manifests/logs outside it, and `compare` distinguishes success, regression and error without hiding failed trials.

Reference test shape:
```python
def test_outputs_cannot_resolve_into_public_checkout(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    alias = tmp_path / 'outside'
    alias.symlink_to(public, target_is_directory=True)
    with pytest.raises(ValueError, match='outside'):
        validate_run_directory(alias / 'logs', public_roots=[public])
```
Implement `validate_run_directory(path: Path, public_roots: list[Path]) -> Path` in runner.py; validate real ancestors for new destinations. The same check applies to logs/rescoring and report writes.

## Task 2: Advisor replay, advice grading and delivery effects

**Files:** Task 2 ownership plus `tests/test_advisor.py`, `fixtures/advisor/{cases.jsonl,variants.json}`.
**Consumes:** EvalCase/Variant/RunConfig; Task 4's OMP adapter only for the live delivery portion.
**Produces:** `tracks.advisor.build_task`, real advise-call capture, calibrated-result artifact and delivery integration.

- [ ] Create paired synthetic cases: unsupported external-state assertion; valid grounded flag; missing/truncated evidence; correctly rejected shortcut; repeated duplicate advice; a correct detection recommending harmful remediation. Do not copy private session text into fixtures.
- [ ] Implement an Inspect advise tool whose arguments mirror the pinned runtime schema (note/severity plus required fields if present). Record structured calls; distinguish no note from an error. Retain the old direct-model-call isolation principle, not its trailing `NONE` instruction.
- [ ] Add fixed-view and frozen-artifact-read modes. Reads operate over an allowlisted in-memory/path-indexed fixture set, not arbitrary local/remote paths. Preserve observer truncation markers and exclude later corrections from input.
- [ ] Supply semantic rubric in a prompt file. Model-grading uses explicit judge model and JSON validation; quote/event evidence is verified against input. Unknowns and malformed grader output remain unscored errors, not automatic negatives. False-alarm and duplicate counts use fixed window/criterion units. Harmful remediation fails advice quality even with correct detection.
- [ ] With the OMP adapter ready, build a sandbox-local pending/running/completed operation fixture and capture advice routing, eligibility and actual state changes. Use runtime steering paths; do not infer prevention from an `isInterruptingSeverity` return or claim already-completed side effects were cancelled.
- [ ] Verification outcome: operator runs advisor fixtures, sees warranted and unwarranted examples with evidence, silence vs errors, and delivery timing vs actual side-effect results in Inspect logs. Human calibration remains explicitly pending for non-human-reviewed real labels.

Acceptance commands after core is available:
```bash
secrets ANTHROPIC_API_KEY -- uv run --project evals agent-evals run --cases evals/fixtures/advisor/cases.jsonl --variants evals/fixtures/advisor/variants.json --model anthropic/claude-opus-4-6 --run-dir /tmp/agent-evaluation-advisor --track advisor --repeats 3
uv run --project evals agent-evals compare --run-dir /tmp/agent-evaluation-advisor
```
Temporary runs here use synthetic data only. Choose a fresh per-attempt directory if an earlier attempt exists; do not delete another run.

## Task 3: Skill catalog activation

**Files:** `tracks/activation.py`, `tests/test_activation.py`, `fixtures/activation/{cases.jsonl,variants.json}`.
**Consumes:** shared types and model configuration; no OMP adapter dependency for the catalog experiment.
**Produces:** `tracks.activation.build_task` and successful-load observations.

- [ ] Write tests for successful skill read, failed read, wrong similarly named skill, late loading, already-loaded skill and no-load negative.
- [ ] Build a frozen competing catalog and a real `read_skill(name)` Inspect tool serving exact bodies. Record identity/body hash and success, not just substring matches or a model saying it loaded something. An already-loaded skill satisfies availability but is not counted as a new invocation.
- [ ] Cases declare deadline semantics in target: first relevant action or named operation. Provide a lightweight relevant-action tool so timing is observed. Do not require skill to be the first tool in every scenario.
- [ ] Use baseline/candidate descriptions with unchanged bodies and remaining catalog order; also exercise additive omission of only the candidate's entry. Include near-miss scenarios sharing vocabulary, not only irrelevant easy negatives.
- [ ] Verification outcome: the operator can inspect which actual body was retrieved, whether it arrived before the relevant action, and both missed and unnecessary activation. Changing a description runs a real paired comparison rather than a claimed route based on prompt wording.

Run the same commands as Task 2 with activation fixture paths, `--track activation` and a fresh `/tmp/agent-evaluation-activation` run directory. Exact command flags remain identical across tracks.

## Task 4: Instruction effects at all tiers and real OMP adapter

**Files:** `tracks/effects.py`, `adapters/omp.py`, `sandbox/{Dockerfile,compose.yaml,scenario_service.py}`, `prompts/simulator*.md`, effect tests/fixtures.
**Consumes:** shared types and run configuration. Task 4 owns `adapters/omp.py` and the following protocol; Task 2 consumes it for delivery tests. Core owns the shared OmpRunResult/OperationEvent models defined above.
```python
async def run_omp_case(case: EvalCase, variant: Variant, config: RunConfig) -> OmpRunResult:
    ...

class OmpProbe(Protocol):
    async def start_operation(self, *, interruptible: bool,
                              hold_at: Literal['queued', 'started', 'committed']) -> str: ...
    async def wait_operation(self, operation_id: str,
                             phase: Literal['queued', 'started', 'committed', 'cancelled'],
                             timeout: float = 15.0) -> None: ...
    async def deliver_advice(self, operation_id: str, note: str,
                             severity: Literal['nit', 'concern', 'blocker']) -> DeliveryReceipt: ...
    async def release_operation(self, operation_id: str) -> None: ...
    async def finish(self) -> OmpRunResult: ...

def open_omp_probe(case: EvalCase, variant: Variant,
                   config: RunConfig) -> AsyncContextManager[OmpProbe]: ...

async def run_isolation_check(run_dir: Path) -> dict[str, JsonValue]: ...
```
These are plan signatures, not implementation stubs. The context manager starts/tears down an actual sandboxed OMP session and independently observable local operation fixture. `start_operation` requests that real operation through OMP's tool path and returns its ID; `wait_operation` observes service/runtime events or raises TimeoutError. `deliver_advice` enters OMP's actual advisor-delivery/steering path. `finish` returns observed transcript, redacted effective API requests, notes, operation events and final service state. Task 2 runs three fresh probes: pending interruptible, running non-interruptible, and already committed. Task 4 provides all lifecycle machinery; Task 2 owns advice cases/scoring. Do not fake this contract with an in-process queue that bypasses OMP.

The operation fixture holds at `hold_at` until `release_operation`; `wait_operation` observes that stable gate. Task 2 waits for the intended phase, delivers operation-scoped advice, checks the receipt/runtime event while the gate remains held, then releases and observes the final state. `committed` holds after mutation, so late advice cannot erase it. Gate control belongs to the independent fixture, not the model; any cancellation claim must come from OMP's actual scheduling path, not from the fixture choosing to suppress a requested operation. Unobserved delivery is an explicit timeout/error, not an inferred receipt.

- [ ] Author one synthetic scenario across all tiers: discover an operation from a supplied API catalog, perform an allowed update, and report accurately. Baseline/candidate instructions differ in schema-enumeration/verification guidance, not hidden requirements. Support agent prompt, context-file text, and skill body as separate insertion points. For skill effects include BOTH `skill_loading='preloaded'` (body supplied through the normal successful-read/tool-result representation before the effect decision) and `skill_loading='catalog'` (only catalog descriptions initially, body must be read). Capture successful read identity/hash and evaluate the subsequent task outcome; a missed load does not remove the trial from effects scoring. At one-shot, catalog mode can only measure the proposed next action, not downstream skill compliance; the simulated/sandbox modes measure full natural-discovery effects.
- [ ] One-shot: ask for the next real response/action, not an explanation of the rule. Score its output against observable requirements; expose criterion evidence.
- [ ] Simulation: tool schemas are stable; simulator gets only environment state and requested call. Validate generated responses/state transitions against the service contract. Reject impossible state as simulator error, return legitimate denial as tool feedback. Use the live configured simulator model, not a scripted fake sold as LLM simulation.
- [ ] Real tier: build a pinned release-based OMP image in the suite's Docker fixture, with no user home or wrappers. Route Anthropic calls through `sandbox_agent_bridge`; expose only required model and fixture services. No host network/socket/mount or ambient credentials. Release download is a build step with explicit version/checksum; do not copy from a personal checkout as the landed installation mechanism.
- [ ] Add `sandbox/isolation_probe.py`, a reusable Docker-only canary probe invoked through the product's sandbox path. It attempts reads of a deliberately unmounted external canary path, access to an absent Docker/SSH socket, metadata-service access and denied network egress under a finite timeout. Use only nonsecret canaries and an owned benign endpoint; never probe live user state. Record each attempted path, denial/outcome and controller-side canary integrity. Refuse to launch the probe unless the resolved provider is Docker. This provides E7 evidence beyond config inspection; expose it as `agent-evals isolation-check --run-dir DIR` through core registration.
- [ ] Run OMP against local scenario files/services. Preserve dynamic prompt composition for agent prompt vs AGENTS/context vs loaded skill. Capture API request structure and actual tool events; verify target provider/model and effective generation settings. If the binary cannot honor a composition, fail that runtime case explicitly and fix the adapter rather than quietly using an Inspect-native agent.
- [ ] Inspect final file/service state before destroying each sandbox. Use immutable images and fresh state/network/home. Implement the OmpProbe handoff above and notify Task 2/Main when usable; no shared adapter edits by Task 2.
- [ ] Verification outcome: one-shot reports response/proposed-action evidence, simulation reports validated simulated state, and real OMP reports actual sandbox state. Reports label each honestly and show both preloaded and natural-discovery skill effects where execution occurs. Candidate comparisons expose disagreement across tiers; repeated trials share no mutable state.

The effects cases file includes all three tiers, so this command exercises the entire tier set:
```bash
secrets ANTHROPIC_API_KEY -- uv run --project evals agent-evals run --cases evals/fixtures/effects/cases.jsonl --variants evals/fixtures/effects/variants.json --model anthropic/claude-opus-4-6 --run-dir /tmp/agent-evaluation-effects --track effects --repeats 3
uv run --project evals agent-evals compare --run-dir /tmp/agent-evaluation-effects
```

## Task 5: MCP wrapper feedback, recovery and hook variants

**Files:** `tracks/tools.py`, package-owned `tool_gateway/{server.py,service.py,raw_cli.py,Dockerfile,compose.yaml}`, `fixtures/tool_gateway/{cases.jsonl,variants.json}`, tool grading prompts/tests.
**Consumes:** shared types/config and Inspect native MCP client APIs. Independent sandbox fixture ownership avoids Task 4 file conflicts.
**Produces:** `tracks.tools.build_task`; deterministic and model-assisted tool wrappers exercised through real MCP transport.

- [ ] Implement a local operation service with state observable independently of the agent. The MCP wrapper accepts only an allowed target/schema, returns actionable denial on an invalid request, and does not mutate state on rejection. The raw CLI path reaches the same sandbox-local service.
- [ ] Compare raw-only, wrapped+raw, and wrapped+restricting-hook variants. Record selection, repaired requests, loops/refusals, bypass attempts and successful side effects. Never call a regex match universal enforcement; the sandbox is the protection for the experiment.
- [ ] Provide deterministic and model-generated feedback variants through the same tool contract. The generator uses `config.feedback_model` and sees only requested call arguments plus environment/service state and its public contract. It cannot see EvalCase.target, hidden criteria, labels, future corrections, judge output or variant identity. Log the effective generator input and assert hidden canary markers are absent; grade feedback and the subject's recovery separately.
- [ ] Execute MCP server and raw/alternate execution routes inside Docker; retain MCP state only within a trial. Validate before/after service state for bad request, corrected request and raw-path attempt.
- [ ] Test legitimate-work preservation: a restrictive hook must not achieve a perfect safety score by preventing all allowed work. Include a same-operation benign case.
- [ ] Verification outcome: an actual MCP tool call returns denial, the subject responds to that feedback and performs a valid operation or records a genuine failure; the report shows actual server state and any alternative execution, not just a tool-call name.

Run using tool_gateway fixture paths, `--track tools` and a fresh `$PRIVATE_EVAL_ROOT/runs/agent-evaluation-tools` child under the attested private root. This is a real local MCP server, not a mocked tool that merely has an MCP-like name.

## Task 6: Historical import, label review, regression promotion and legacy cutover

**Files:** `src/agent_evals/corpus.py`, `tests/test_corpus.py`, `fixtures/import/` synthetic OMP/OpenCode/Claude sources; `plugins/sjawhar/skills/skill-evals/SKILL.md`; remove its `scripts/{extract_cases,run_eval,judge,_anthropic}.py` after replacing all supported contracts.
**Consumes:** EvalCase/Event schemas. CLI hooks provided to core:
```python
def import_legacy(legacy_dir: Path, sources_file: Path, data_dir: Path) -> dict[str, JsonValue]: ...
def promote_case(case_id: str, cases_path: Path, decision_path: Path) -> Path: ...
```
**Produces:** reconstructable private cases/admission ledger, public synthetic import fixtures, current operator workflow, regression promotion.

- [ ] Rebuild source parsers from supported recorded formats. OMP keeps `toolCall` blocks and `toolResult` messages; Claude keeps tool_use/tool_result; OpenCode joins message/part records and preserves completed/error tool state. Original block order and IDs survive normalization. SQLite reads use read-only URIs; never migrate the user's session DB.
- [ ] Resolve exact source session/event references. Legacy fragment/turn locators need unambiguous content+timestamp agreement, not the old nearest-message fallback. Emit one admission ledger outcome for every input candidate, including rejected or missing cases; unknown source formats error loudly.
- [ ] Exclude later corrections/expected labels from subject windows. Preserve truncation metadata and coherent call/result pairs. Skip a case only with an explicit reason and count. Import old labels as pending; machine reminders/no human intervention are not automatically negative examples.
- [ ] Require external data directory. For actual migration verify destination privacy and version control before processing; scan/redact secrets and review sensitive data before storing/sendable views. Unresolved findings quarantine cases. Record source/stored hashes and redaction provenance. Never invent a private remote or use user credentials to make one without approval.
- [ ] Implement explicit label-review decisions and regression promotion with provenance; a later deliberate regression must be caught by running the promoted case. No automatic promotion based on an LLM grade.
- [ ] Integrate import/promote functions with core CLI, update skill-evals usage, and remove legacy code without maintaining compatibility aliases. Demonstrate parser/migration/compare behavior on synthetic source fixtures before deletion. Preserve legacy data until actual private copy hashes+versioned backup are verified; source sessions untouched.
- [ ] Verification outcome: run the import CLI on synthetic fixtures, inspect retained tool events and pending labels, reject an ambiguous locator, promote a reviewed case and catch its later regression. Then, once the private repository is approved, inventory and migrate the complete real legacy population, verify durable private revision and reconcile counts; do not execute real-history model evals until labels/privacy approval exist.

Representative commands:
```bash
uv run --project evals agent-evals import --legacy evals/fixtures/import/legacy --sources evals/fixtures/import/sources.json --data-dir /tmp/agent-evaluation-import
uv run --project evals agent-evals validate --cases /tmp/agent-evaluation-import/cases.jsonl
uv run --project evals agent-evals promote --case-id synthetic-regression --cases /tmp/agent-evaluation-import/cases.jsonl --decision evals/fixtures/import/review-decision.json
```
The synthetic exception is confined to the package's bundled import fixture root with verified fixture digests. Arbitrary source metadata cannot opt a real-history import into this path. `sources.json` has `{"omp_roots": [...], "opencode_roots": [...], "claude_roots": [...]}` with paths resolved relative to that file; resolve each case's exact source session, including session filtering inside any shared OpenCode database. A model-free privacy/durability check still runs on synthetic output paths.

## Task 7: Integration, acceptance, review and shipping

**Owner:** Main coordinates; `deep` fixes and performs acceptance, `reviewer` gates. Main does not implement missing code.

- [ ] Review each worker's files and evidence against its task. Dispatch mapped reviewers over owned diffs. A report with only tests or inspection is incomplete; return it to the implementer with the operator surface named.
- [ ] Run project formatting/checks once after workers finish mutating shared imports: `uv run --project evals pytest evals/tests` plus the formatter/linter configured by Task 1. Dispatch failures to `deep`; do not patch inline.
- [ ] Dispatch acceptance using E1–E9 below. Under this approved scope, mandatory real surfaces (E3's OMP/Docker segment, E4's MCP transport and E7's isolation checks) must be RAN or BLOCKED, never silently substituted or waived by an agent. Other `WAIVED-BY-SAMI` entries require his explicit words and scope. Sami may explicitly change the scope, but then amend the spec/acceptance requirement; a generic waiver cannot count as completing the unchanged spec. Missing credentials/private storage are exact blockers, not permission to claim synthetic execution as historical acceptance.
- [ ] Final mapped reviewer checks complete spec coverage and separately looks for shims/aliases, duplicate old/new paths, dead code, TODO/FIXME markers, incomplete migrations and reduced-scope language. Every actionable finding goes to `deep`, with ledger entries reconciled.
- [ ] Run `opening-a-pr` as coordinator for the empty-ledger and acceptance gate. This repo's shipping convention is direct main, no PR; do not invent a multi-PR stack. Snapshot only this workspace's changes and integrate the approved delta without overwriting primary-checkout work. Remote/merge actions follow the actual repository gate; do not publish other agents' dirty parent changes.

## End-to-end verification plan

Tooling already verified: `uv 0.12.5`, Inspect 0.3.263 through uvx, Docker Engine 29.7.2 with Compose v5.4.0. Model listing authenticates with the agent-tier Anthropic key and includes `claude-opus-4-6`. Existing product tooling: Inspect `eval`, `sandbox_agent_bridge`, native MCP clients, `.eval` logs and `inspect view` (official docs under inspect.aisi.org.uk). Missing suite-specific tooling is owned by Tasks 1 and 6, not deferred.

| ID | Real operator-facing path | Required observed outcome |
|---|---|---|
| E1 | `agent-evals run` + `compare` on advisor fixtures | Notes/silence/errors distinct, evidence for wrong/right advice, harmful remedy fails, repeats visible and burden computed correctly. |
| E2 | Same CLI on activation fixtures | Successful and failed/late reads shown against competing catalog; missed and unnecessary loads both scored. |
| E3 | Same CLI on effects fixtures | One-shot produces response/proposed-action evidence; LLM simulation produces validated simulated-state evidence; OMP-in-Docker produces actual state evidence. Both natural-discovery and preloaded skill effects are exercised in rollout tiers. Captured requests verify insertion points/noncandidate inputs. |
| E4 | Same CLI on tool_gateway fixtures | Real MCP transport, invalid request denial, correction or observed failed recovery, actual allowed mutation, raw/guarded bypass results, model-generated feedback graded. |
| E5 | Advisor delivery integration through OMP sandbox | Pending interruptible/running/completed fixture calls tested; actual side effects agree with reported prevention/late advice, not inferred from severity alone. |
| E6 | `agent-evals import`, `validate`, `promote`, then rerun | Synthetic source events reconstructed without dropped tools; ambiguous locator refused; pending vs human-reviewed labels preserved; later regression detected from saved case. |
| E7 | `agent-evals isolation-check --run-dir DIR`, run-directory rejection and two independent sandbox trials | Nonsecret filesystem/socket/network probes produce observed denials, controller canary stays intact, sibling state is absent, public-tree logs refused before model/output, and errors remain recorded. |
| E8 | `inspect view --log-dir RUN_DIR`, real browser via browser tool | Open a paired result, inspect transcript/tool calls and evidence, find one failure and one comparison. No custom viewer; no claim based only on file existence or HTTP liveness. |
| E9 | Real corpus import to approved private repository | Every legacy candidate accounted for; copied/redacted artifacts verified and versioned privately; no historical model run before review; no unrelated files removed. Pending repository authority is a named gate, not a code-work blocker. |

Acceptance is run after final code mutations, and affected scenarios are re-run after fixes. RAN evidence must describe what was observed, not just paste the invocation. Synthetic sample outcomes measure product operation; they are not a claim that prompts have been calibrated against human labels.

## Hardening ledger

Empty at plan creation. Workers append shortcuts immediately and return them with their reports. Main records rulings here and pays every actionable entry before the shipping gate.

Plan-review ruling: all nine findings are addressed. The waiver finding is applied as an agent-side prohibition on downgrading mandatory scope, not as a restriction on Sami's authority to change the spec explicitly. No task scope reduced.

Ruling: `Variant.omit_skills` is a unique list of catalog entries excluded before applying skill overrides; overlap with override keys is rejected. This keeps intervention-specific selection out of case.environment and preserves remaining catalog order. Core owns schema; activation/effects consume it.

Ruling: `EvalCase.unavailable_tool_call_ids` explicitly identifies omitted calls in observer windows. Unique IDs only; it is not an automatic repair for unresolved source data. Ordinary unmatched results are rejected. Fixed transcript views may show unavailable-context markers; agentic/API replay cannot submit unmatched native tool messages and rejects that incomplete history. Core owns schema; corpus and advisor consume it.

Ruling: preloaded skill bodies are selected by explicit public `case.environment.preloaded_skills`, validated against the effective catalog, when variant.skill_loading is preloaded. Never derive them from target/criteria. Missing/unknown preload names are admission errors. Changing hidden target labels must not change any subject-visible input. Activation and effects own their consumers; no new shared model field is needed.

Paid — native unavailable scores: effects and advisor use `Score.unscored()`; comparison converts native NaN/Infinity to null and excludes unsupported pairs. Core, advisor and effects reviewers approved the fixes. Fresh CLI logs show zero scored / one unscored sample for unavailable results; normal results remain numeric. Advisor uses the same Inspect-native per-sample `mean()` convention as effects.

Paid — sandbox operation attestation: independent native positive and seeded-active/no-ledger runs verify the exact controller operation/project/status evidence. Matching state or plausible model prose alone cannot pass. The final role-fidelity and simulated-effects paths were independently re-gated; ordinary effects behavior was unchanged by the native advisor phase cutover.

Paid — historical privacy, normalization and durability: independent final audit accounts for 856 candidates (338 pending, 469 rejected, 49 quarantined), validates every stored/source hash and event origin, and finds no retained source-root or unresolved-secret residue. All 25 private remote files match local bytes, including the 21-file indexed collection; the superseded generated monolith remains in private history, not the current tree. No historical model use occurred.

Paid — native scheduling fidelity: direct extension registration preserves scheduling fields. Queued controls establish the real same-message batch, held approval, ACK-before-target ordering and native result; backend phases establish running/late operations. Independent snapshots distinguish a skipped target from actual execution. No severity or fixture-phase inference substitutes for that evidence.

Paid — fixture administration boundary: EvalFixtureControl isolated controls on scenario-loopback 8081 and added real isolation-check observations. Independent re-review approved exact denial semantics, required positive controls and six complete unchanged controller snapshots. The strict real Docker run reports safe:true; command success, missing commands, reachable HTTP errors and malformed snapshots cannot masquerade as isolation.

Paid — faithful artifact replay and collection cutover: indexed case/blob handling, source-defined normalization, linked native image replay, subject/judge ordering, exact ID/SHA evidence and all negative input/privacy paths passed scoped reviews and independent operator acceptance. Raw synthetic image import, explicit promotion and native Opus replay also ran end to end.

Paid — native preload and partial startup: independent re-review approved actual matching successful, non-truncated read-result correlation and safe pre-RPC cleanup. Adversarial tests reject prompt echoes, failed/truncated/missing reads; startup keeps its original error and trace. The post-fix real four-variant run succeeds without admission errors.

Paid — private-child destination: storage/caller checks preserve the nearest attested repository boundary; README and skill distinguish an attested root from fresh non-symlinked children. Independent review approved the fix and literal documented import/validate flow.

Paid — independent native phases: the instrumented packed diagnostic observed streaming state settling but did not identify the earlier persistent wait. The cutover removes the unrelated between-phase idle dependency. Independent installed acceptance completed all 18 records: three phases, three repeats and two variants, with six queued no-steer controls. Every operation has complete evidence and a bounded post-observation snapshot. Busy state remains visible; neither recovery nor teardown-based prevention is claimed.

Source-refresh recovery: a stale jj update selected an old divergent revision. Main restored the preserved complete snapshot and the exact approved design, ignore rules and lockfile from its pre-update archive. Every owned file's bytes/mode matches; only an obsolete empty legacy directory disappeared. Post-restore CLI validation returned all six activation cases.

Paid — final audit boundaries: strict source/judge/simulator values, full frozen-manifest trial accounting, lexical private-path admission, required veto scores, installed resource discovery and one terminal gateway assessment per sample passed scoped reviews and independent CLI checks. Invalid frozen-read paths return tool feedback without granting access or aborting the sample.

Paid — installed operator acceptance: all 18 normal MCP samples and the fresh complete 18-sample provider-error control retained real state and original errors; Docker isolation passed its denials, positive controls and integrity checks. A prior Compose timeout/retry left an owned partial container; its metadata and failed run were retained, only that verified orphan was removed, and the control was rerun unchanged. No retry logic, deadlines or model outcomes were tuned.

Ledger balance: no unpaid implementation shortcut or blocked acceptance scenario. Historical label/privacy review remains an intentional prerequisite for future historical model use, not unfinished suite implementation.
