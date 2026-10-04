// Proactive memory agent — the two-phase memory intervention architecture of Wu et al.,
// "Remember When It Matters" (arXiv 2607.08716, §3), on Oh My Pi's context hook. The prompt
// texts in ./proactive-memory-*.md, the tool names, descriptions and parameter text in
// BANK_TOOL_SPECS, the bank rendering in formatBank, the Phase 2 parse rule and the BM25
// prefilter are copied from the authors' released implementation,
// https://github.com/yifannnwu/proactive-memory-agent at commit
// 89e5c0d6aadfe531a1aee42fd290d48be89973dd (src/memory_agent/memory/memory_agent.py,
// src/memory_agent/memory_enabled_agent.py, src/memory_agent/memory/bm25_search.py),
// Apache License 2.0 — see ./LICENSE.proactive-memory-agent. Only the observation rendering,
// the delivery of the reminder and the operational additions below are ours.
//
// Trigger: the `context` hook, awaited by the loop before every provider request. The request's
// tail says what it is (classifyRequest): a prompt or steer starts a run's first step; tool
// results or a post-compaction `developer` continuation are a continuation, run every
// MEMORY_INTERVAL_TURNS turns; a side turn (/btw, handoff) or a live-steer batch runs nothing. A
// step is keyed on the request's own fingerprint, not a turn counter, so a provider auto-retry
// that re-sends an identical request re-sends the previous reminder instead of running a new
// step; a compaction (its id changing on the branch) always runs a step, whatever the turn. An
// auto-shake retry keeps the same message count and tail timestamp as the request it retries
// (fork session-maintenance.ts), so it hits this same `key === lastKey` retry-reuse path: no new
// step runs, and the previous reminder (if any) rides the retried request. This is intentional
// (Deviation 19) — do not special-case it.
//
// Deadline: STEP_DEADLINE_MS per step, inside the fork's own 30 s extension-handler cap. Phase 2
// only starts with PHASE2_MIN_REMAINING_MS left. Every failure path — skip, error, abort,
// deadline, a thrown exception anywhere in the handler — leaves the action agent's turn exactly
// as it would have been without the extension: the hook resolves `undefined`.
//
// Persistence: the bank is a `custom` session entry (BANK_ENTRY_TYPE), never sent to the model
// and never rewritten by compaction; each step is recorded as a STEP_ENTRY_TYPE entry, and each
// observed delivery of a reminder onto the wire as a DELIVERY_ENTRY_TYPE entry.
//
// Switch: none of its own. It runs while the experiments extension's `proactive_memory` gate is
// on for the session (index.ts).

export const MEMORY_INTERVAL_TURNS = 1; // At 1, every turn waits for the two-call memory step, bounded by STEP_DEADLINE_MS — a reader meets that consequence here, not by tracing call sites.
export const WINDOW_TURNS = 8;
export const STEP_DEADLINE_MS = 25_000;
export const PHASE2_MIN_REMAINING_MS = 4_000;
export const PHASE1_MAX_TOKENS = 4_096;
export const PHASE2_MAX_TOKENS = 1_024;
export const MEMORY_TEMPERATURE = 0.3;
export const MAX_STATUS_CHARS = 2_000;
export const ID_LENGTH = 8;
export const MAX_MEMORY_IN_PROMPT = 50;
export const PREFILTER_TOP_K = 20;
export const RESULT_CAP_BYTES = 10_240;
export const TEXT_CAP_BYTES = 8_192;
export const COMMANDS_SHOWN = 5;
export const ARGS_CAP_CHARS = 300;
export const BANK_ENTRY_TYPE = "proactive-memory-bank";
export const STEP_ENTRY_TYPE = "proactive-memory-step";
export const DELIVERY_ENTRY_TYPE = "proactive-memory-delivery";
export const MEMORY_ROLE = "@proactiveMemory";
export const LEGION_ENV = ["LEGION_TREE", "LEGION_ROLE", "LEGION_CONTROLLER"] as const;
export const ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const BM25_K1 = 1.5;
const BM25_B = 0.75;

import phase1SystemText from "./proactive-memory-phase1-system.md" with { type: "text" };
import phase1UserText from "./proactive-memory-phase1-user.md" with { type: "text" };
import phase2SystemText from "./proactive-memory-phase2-system.md" with { type: "text" };
import phase2UserText from "./proactive-memory-phase2-user.md" with { type: "text" };
import injectionText from "./proactive-memory-injection.md" with { type: "text" };

export const PHASE1_SYSTEM = phase1SystemText.trim();
export const PHASE1_USER = phase1UserText.trim();
export const PHASE2_SYSTEM = phase2SystemText.trim();
export const PHASE2_USER = phase2UserText.trim();
export const INJECTION = injectionText.trim();

export interface MemoryEntry {
	id: string;
	content: string;
	created_at: string;
	access_count: number;
	last_accessed: string | null;
}
export interface Bank {
	version: 1;
	turn: number;
	status: string;
	knowledge: MemoryEntry[];
	procedural: MemoryEntry[];
}
export type ToolSpec = { name: string; description: string; parameter: "content" | "memory_id"; parameterDescription: string };
export const BANK_TOOL_SPECS: readonly ToolSpec[] = [
	{
		name: "memory_save_knowledge",
		description:
			"Save an important fact to the knowledge bank. Use for: task requirements, environment facts, file paths, API details, key constraints from the task description.",
		parameter: "content",
		parameterDescription: "The fact to save. Be precise and specific.",
	},
	{
		name: "memory_save_procedural",
		description:
			"Record a debugging experience, failed approach, or solution. Use for: error patterns, things that didn't work and why, successful fixes, performance observations.",
		parameter: "content",
		parameterDescription: "The experience to record. Include what was tried and what happened.",
	},
	{
		name: "memory_update_status",
		description: "Update your internal tracking of task progress. This is for YOUR reference only, not shown to the action agent.",
		parameter: "content",
		parameterDescription: "Current task status/progress summary.",
	},
	{
		name: "memory_delete",
		description: "Delete an outdated or incorrect memory entry by its ID.",
		parameter: "memory_id",
		parameterDescription: "The ID of the memory to delete.",
	},
];

/** A `content` block of an `AgentMessage`: text, a tool call, or anything else (thinking, images), which renderObservation ignores. */
export type MsgBlock = { type: "text"; text: string } | { type: "toolCall"; name: string; arguments: Record<string, unknown> } | { type: string; text?: string };
/** The structural subset of `AgentMessage` the core reads. */
export interface Msg {
	role: string;
	content?: string | Array<MsgBlock>;
	attribution?: string;
	steering?: boolean;
	customType?: string;
	toolName?: string;
	isError?: boolean;
	timestamp?: number;
	summary?: string;
	command?: string;
	output?: string;
	code?: string;
	excludeFromContext?: boolean;
	files?: Array<{ path: string }>;
}
/** The structural subset of `SessionEntry` the core reads. */
export interface BranchEntry {
	id: string;
	type: string;
	customType?: string;
	data?: unknown;
	message?: { role: string; stopReason?: string; retryRecovery?: unknown };
}

export type RequestKind =
	// `tailCount` is the number of trailing messages (from `messages`, the SAME array this kind
	// was classified from) that express this run's own start — 1 for a plain user prompt, or the
	// whole trailing custom/hookMessage/fileMention block for a run a harness event started. It is
	// a count, not a message reference: a count survives the fork's `structuredClone` of the array
	// on every later `context` call trivially (it's a number), where a stored object reference does
	// not (`runner.ts:1906-1914`) — the cause of the F7/W1 regression a round-1 fix introduced by
	// remembering *which message* started the run instead of *how many turns* have happened since.
	// `tailCount` is used only in the SAME call it is produced: renderObservation trims it off
	// before windowing so the run's own opening text isn't echoed back as `[New Input]`. No run
	// boundary is remembered across calls at all — `state.runStartTurn` (a turn count, immune to
	// cloning and to reload by construction) is the only thing a later call needs, and it was
	// already the only source of truth `loadState` restores on resume.
	| { kind: "prompt"; text: string; tailCount: number }
	| { kind: "steer"; text: string }
	| { kind: "continuation" }
	| { kind: "side" };

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Joins a message's string content or its text blocks. */
export function text(m: Msg): string {
	if (typeof m.content === "string") return m.content;
	if (Array.isArray(m.content)) return m.content.filter(b => b.type === "text" && typeof b.text === "string").map(b => b.text as string).join("\n");
	return "";
}

/** The tool-call blocks of an assistant message's content, in order. */
function toolCallsOf(m: Msg): Array<{ name: string; arguments: Record<string, unknown> }> {
	if (!Array.isArray(m.content)) return [];
	return m.content.filter((b): b is { type: "toolCall"; name: string; arguments: Record<string, unknown> } => b.type === "toolCall").map(b => ({ name: b.name, arguments: b.arguments }));
}

/** Maps a completion's stop reason to a `StepRecord`/`Completion` error, or undefined when the completion is usable as-is. Mirrors the fork's `StopReason`: an error and an abort both stop the run; a `length` cutoff means the model's tool calls may be truncated mid-argument, so Phase 1/2 must not apply them. */
export function mapStopReason(stopReason: string | undefined, errorMessage: string | undefined): string | undefined {
	if (stopReason === "error") return errorMessage ?? "error";
	if (stopReason === "aborted") return "aborted";
	if (stopReason === "length") return "length";
	return undefined;
}

export function classifyRequest(messages: readonly Msg[]): RequestKind {
	if (messages.length === 0) return { kind: "side" };
	// The live-steer path hands the hook the dequeued steering batch alone (agent-loop.ts:1838-1869).
	if (
		!messages.some(m => m.role === "assistant") &&
		messages.every(m => m.role === "custom" || m.role === "hookMessage" || m.role === "fileMention" || (m.role === "user" && m.steering === true))
	)
		return { kind: "side" };
	const trailing: Msg[] = [];
	let i = messages.length - 1;
	for (; i >= 0 && (messages[i].role === "custom" || messages[i].role === "hookMessage" || messages[i].role === "fileMention"); i--) trailing.unshift(messages[i]);
	const m = messages[i];
	if (!m) return { kind: "side" };
	if (m.role === "toolResult" || m.role === "developer" || m.role === "bashExecution" || m.role === "pythonExecution") return { kind: "continuation" };
	if (m.role === "user") {
		if (m.steering === true) return { kind: "steer", text: text(m) };
		if (m.attribution === "agent") return { kind: "side" }; // /btw, /omfg, handoff (agent-session.ts:10420-10425)
		return { kind: "prompt", text: text(m), tailCount: 1 };
	}
	if (m.role === "assistant" && trailing.length > 0) {
		const spoken = trailing.filter(t => t.role !== "fileMention");
		// The window must exclude the whole trailing block, not just its first message, so every
		// message that fed the task description is excluded from the trajectory the same way a plain
		// user prompt is (finding 3).
		return { kind: "prompt", text: spoken.map(text).join("\n\n"), tailCount: trailing.length };
	}
	return { kind: "side" };
}

export function fingerprint(messages: readonly Msg[]): string {
	const tail = messages[messages.length - 1];
	return `${messages.length}:${tail?.role ?? ""}:${tail?.timestamp ?? ""}`;
}

export function shouldRun(
	kind: RequestKind,
	key: string,
	lastKey: string | undefined,
	turn: number,
	lastMemoryTurn: number,
	compactionMoved: boolean,
): boolean {
	if (kind.kind === "side") return false;
	if (key === lastKey) return false;
	if (compactionMoved) return true;
	if (kind.kind === "prompt" || kind.kind === "steer") return true;
	return turn - lastMemoryTurn >= MEMORY_INTERVAL_TURNS;
}

export function latestCompactionId(branch: readonly BranchEntry[]): string | undefined {
	for (let i = branch.length - 1; i >= 0; i--) if (branch[i].type === "compaction") return branch[i].id;
	return undefined;
}

export function latestResetBoundaryId(branch: readonly BranchEntry[]): string | undefined {
	for (let i = branch.length - 1; i >= 0; i--) if (branch[i].type === "reset_boundary") return branch[i].id;
	return undefined;
}

export function countTurns(branch: readonly BranchEntry[]): number {
	let n = 0;
	for (const entry of branch) {
		if (entry.type !== "message" || entry.message?.role !== "assistant") continue;
		if (entry.message.retryRecovery !== undefined) continue;
		if (entry.message.stopReason === "error") continue;
		n++;
	}
	return n + 1;
}

function sliceAfterBoundary(branch: readonly BranchEntry[], boundaryId: string | undefined): readonly BranchEntry[] {
	if (boundaryId === undefined) return branch;
	const idx = branch.findIndex(e => e.id === boundaryId);
	return idx === -1 ? branch : branch.slice(idx + 1);
}

function findLastCustom<T>(entries: readonly BranchEntry[], customType: string): { entry: BranchEntry; data: T } | undefined {
	for (let i = entries.length - 1; i >= 0; i--) {
		const e = entries[i];
		if (e.type === "custom" && e.customType === customType) return { entry: e, data: e.data as T };
	}
	return undefined;
}

interface StepData {
	turn: number;
	runStep: number;
	key: string;
	taskDescription?: string;
}

/** A `MemoryEntry`-shaped record: every array member loadState trusts must satisfy this, or a
 * single malformed entry (`knowledge: [null]`, say) reaches `ids()`/`formatBank()` downstream and
 * throws past the point the bank was supposed to have been validated. */
function isValidMemoryEntry(value: unknown): value is MemoryEntry {
	return (
		isRecord(value) &&
		typeof value.id === "string" &&
		typeof value.content === "string" &&
		typeof value.created_at === "string" &&
		typeof value.access_count === "number" &&
		(value.last_accessed === null || typeof value.last_accessed === "string")
	);
}

/** A `Bank`-shaped record: the fields loadState needs before trusting a persisted entry's data. A
 * malformed entry (a schema the extension never wrote — hand-edited, or from a future version)
 * must not crash every future context call; it is treated as absent instead. Every array member is
 * checked too, not just the arrays' presence: a bank whose `knowledge` holds one malformed entry
 * (`[null]`, say) is exactly as untrusted as one whose `knowledge` isn't an array at all. */
function isValidBank(value: unknown): value is Bank {
	return (
		isRecord(value) &&
		value.version === 1 &&
		typeof value.status === "string" &&
		Array.isArray(value.knowledge) &&
		Array.isArray(value.procedural) &&
		value.knowledge.every(isValidMemoryEntry) &&
		value.procedural.every(isValidMemoryEntry)
	);
}

function isValidStepData(value: unknown): value is StepData {
	return isRecord(value) && typeof value.turn === "number" && typeof value.runStep === "number" && typeof value.key === "string";
}

export function loadState(branch: readonly BranchEntry[]): {
	bank: Bank;
	lastMemoryTurn: number;
	lastKey?: string;
	taskDescription: string;
	runStartTurn: number;
	resetBoundaryId?: string;
	compactionId?: string;
} {
	const resetBoundaryId = latestResetBoundaryId(branch);
	const slice = sliceAfterBoundary(branch, resetBoundaryId);
	const bankEntry = findLastCustom<unknown>(slice, BANK_ENTRY_TYPE);
	const bank = bankEntry && isValidBank(bankEntry.data) ? bankEntry.data : emptyBank();

	let lastStepEntry: BranchEntry | undefined;
	let lastStepData: StepData | undefined;
	for (let i = slice.length - 1; i >= 0; i--) {
		const e = slice[i];
		if (e.type === "custom" && e.customType === STEP_ENTRY_TYPE && isValidStepData(e.data)) {
			lastStepEntry = e;
			lastStepData = e.data;
			break;
		}
	}

	let taskDescription = "";
	let runStartTurn = 1;
	for (let i = slice.length - 1; i >= 0; i--) {
		const e = slice[i];
		if (e.type === "custom" && e.customType === STEP_ENTRY_TYPE && isValidStepData(e.data)) {
			if (e.data.taskDescription !== undefined) {
				taskDescription = e.data.taskDescription;
				runStartTurn = e.data.turn - e.data.runStep + 1;
				break;
			}
		}
	}

	// The latest compaction id at or before the newest step entry, so a compaction after it counts as moved.
	const lastStepIndex = lastStepEntry ? slice.indexOf(lastStepEntry) : slice.length - 1;
	let compactionId: string | undefined;
	for (let i = Math.min(lastStepIndex, slice.length - 1); i >= 0; i--) {
		if (slice[i].type === "compaction") {
			compactionId = slice[i].id;
			break;
		}
	}

	return {
		bank,
		lastMemoryTurn: lastStepData?.turn ?? 0,
		lastKey: lastStepData?.key,
		taskDescription,
		runStartTurn,
		resetBoundaryId,
		compactionId,
	};
}

export function emptyBank(): Bank {
	return { version: 1, turn: 0, status: "", knowledge: [], procedural: [] };
}

function ids(bank: Bank): { knowledge: string[]; procedural: string[] } {
	return { knowledge: bank.knowledge.map(e => e.id), procedural: bank.procedural.map(e => e.id) };
}

function counts(bank: Bank): { knowledge: number; procedural: number; statusChars: number } {
	return { knowledge: bank.knowledge.length, procedural: bank.procedural.length, statusChars: bank.status.length };
}

export type Operation = { action: "save_knowledge" | "save_procedural" | "update_status" | "delete"; id?: string; content?: string; ok: boolean; error?: string };

function existingIds(bank: Bank): Set<string> {
	return new Set([...bank.knowledge.map(e => e.id), ...bank.procedural.map(e => e.id)]);
}

function freshId(newId: () => string, taken: Set<string>): string {
	let id = newId();
	while (taken.has(id)) id = newId();
	taken.add(id);
	return id;
}

export function applyOperations(
	bank: Bank,
	calls: readonly { name: string; arguments: Record<string, unknown> }[],
	now: () => string,
	newId: () => string,
): { bank: Bank; operations: Operation[]; changed: boolean } {
	const next: Bank = { ...bank, knowledge: [...bank.knowledge], procedural: [...bank.procedural] };
	const operations: Operation[] = [];
	let changed = false;
	const taken = existingIds(next);
	for (const call of calls) {
		if (call.name === "memory_save_knowledge" || call.name === "memory_save_procedural") {
			const content = typeof call.arguments.content === "string" ? call.arguments.content : "";
			if (!content) {
				operations.push({ action: call.name === "memory_save_knowledge" ? "save_knowledge" : "save_procedural", ok: false, error: "empty content" });
				continue;
			}
			const id = freshId(newId, taken);
			const entry: MemoryEntry = { id, content, created_at: now(), access_count: 0, last_accessed: null };
			if (call.name === "memory_save_knowledge") next.knowledge.push(entry);
			else next.procedural.push(entry);
			changed = true;
			operations.push({ action: call.name === "memory_save_knowledge" ? "save_knowledge" : "save_procedural", id, content, ok: true });
		} else if (call.name === "memory_update_status") {
			const content = typeof call.arguments.content === "string" ? call.arguments.content : "";
			// Deviation from upstream: universal_memory.py clears the status on empty input. Here an
			// empty update_status is rejected (ok:false, no change) instead — more defensive, chosen
			// deliberately, not a bug.
			if (!content) {
				operations.push({ action: "update_status", ok: false, error: "empty content" });
				continue;
			}
			next.status = content.length > MAX_STATUS_CHARS ? content.slice(0, MAX_STATUS_CHARS) + "..." : content;
			changed = true;
			operations.push({ action: "update_status", content: next.status, ok: true });
		} else if (call.name === "memory_delete") {
			const id = typeof call.arguments.memory_id === "string" ? call.arguments.memory_id : "";
			const ki = next.knowledge.findIndex(e => e.id === id);
			if (ki !== -1) {
				next.knowledge.splice(ki, 1);
				changed = true;
				operations.push({ action: "delete", id, ok: true });
				continue;
			}
			const pi = next.procedural.findIndex(e => e.id === id);
			if (pi !== -1) {
				next.procedural.splice(pi, 1);
				changed = true;
				operations.push({ action: "delete", id, ok: true });
				continue;
			}
			operations.push({ action: "delete", id, ok: false, error: "ID not found" });
		}
		// any other tool name is skipped without an operation
	}
	return { bank: next, operations, changed };
}

function formatMemoryList(entries: readonly MemoryEntry[]): string {
	if (entries.length === 0) return "(empty)";
	return entries.map(e => `[${e.id}] ${e.content}`).join("\n");
}

/** The entries formatBank actually renders: everything, or (once the bank exceeds
 * MAX_MEMORY_IN_PROMPT) the BM25 top-PREFILTER_TOP_K per section against `observation`. Shared by
 * formatBank and the factory's `bankShown` bookkeeping so the two can never disagree about what
 * the memory agent was shown. */
function selectShownEntries(bank: Bank, observation: string): { knowledge: MemoryEntry[]; procedural: MemoryEntry[]; truncated: boolean } {
	const total = bank.knowledge.length + bank.procedural.length;
	if (total <= MAX_MEMORY_IN_PROMPT) return { knowledge: bank.knowledge, procedural: bank.procedural, truncated: false };
	const kIdx = bm25TopK(observation, bank.knowledge.map(e => e.content), PREFILTER_TOP_K);
	const pIdx = bm25TopK(observation, bank.procedural.map(e => e.content), PREFILTER_TOP_K);
	return { knowledge: kIdx.map(i => bank.knowledge[i]), procedural: pIdx.map(i => bank.procedural[i]), truncated: true };
}

/** The ids formatBank(bank, observation) actually renders into the prompt — what the memory agent
 * saw, as distinct from every id the bank holds (see StepRecord.bankBefore vs bankShown). */
export function bankShownIds(bank: Bank, observation: string): { knowledge: string[]; procedural: string[] } {
	const shown = selectShownEntries(bank, observation);
	return { knowledge: shown.knowledge.map(e => e.id), procedural: shown.procedural.map(e => e.id) };
}

export function formatBank(bank: Bank, observation: string): string {
	const kCount = bank.knowledge.length;
	const pCount = bank.procedural.length;
	const shown = selectShownEntries(bank, observation);
	let knowledgeStr = formatMemoryList(shown.knowledge);
	let proceduralStr = formatMemoryList(shown.procedural);
	if (shown.truncated) {
		knowledgeStr += `\n(showing top ${PREFILTER_TOP_K} of ${kCount})`;
		proceduralStr += `\n(showing top ${PREFILTER_TOP_K} of ${pCount})`;
	}
	return `<memory_bank>\n<status>${bank.status || "(no status)"}</status>\n<knowledge>\n${knowledgeStr}\n</knowledge>\n<procedural>\n${proceduralStr}\n</procedural>\n</memory_bank>`;
}

function tokenize(text: string): string[] {
	return text.toLowerCase().match(/\b\w+\b/g) ?? [];
}

export function bm25TopK(query: string, documents: readonly string[], k: number): number[] {
	if (documents.length === 0) return [];
	const queryTokens = tokenize(query);
	if (queryTokens.length === 0) return [];
	const docTokens = documents.map(tokenize);
	const docLengths = docTokens.map(t => t.length);
	const nDocs = documents.length;
	const avgdl = docLengths.reduce((a, b) => a + b, 0) / nDocs || 0;
	const docTermFreqs = docTokens.map(tokens => {
		const m = new Map<string, number>();
		for (const t of tokens) m.set(t, (m.get(t) ?? 0) + 1);
		return m;
	});
	const docFreq = new Map<string, number>();
	for (const tokens of docTokens) {
		for (const t of new Set(tokens)) docFreq.set(t, (docFreq.get(t) ?? 0) + 1);
	}
	const idf = (term: string) => {
		const df = docFreq.get(term) ?? 0;
		return Math.log((nDocs - df + 0.5) / (df + 0.5) + 1);
	};
	const scores: [number, number][] = [];
	for (let i = 0; i < nDocs; i++) {
		let score = 0;
		const docLen = docLengths[i];
		const termFreqs = docTermFreqs[i];
		for (const term of queryTokens) {
			const tf = termFreqs.get(term);
			if (tf === undefined) continue;
			const idfVal = idf(term);
			const numerator = tf * (BM25_K1 + 1);
			const denominator = tf + BM25_K1 * (1 - BM25_B + (BM25_B * docLen) / (avgdl || 1));
			score += (idfVal * numerator) / denominator;
		}
		if (score > 0) scores.push([i, score]);
	}
	scores.sort((a, b) => b[1] - a[1]);
	return scores.slice(0, k).map(([i]) => i);
}

export function parsePhase2(reply: string): string | null {
	const match = /<context_for_action>([\s\S]*?)<\/context_for_action>/.exec(reply);
	if (match) {
		const context = match[1].trim();
		if (context) return context;
	}
	if (reply.includes("<no_intervention")) return null;
	return null;
}

export function render(template: string, vars: Record<string, string>): string {
	return template.replace(/\{\{(\w+)\}\}/g, (whole, key: string) => (Object.prototype.hasOwnProperty.call(vars, key) ? vars[key] : whole));
}

/** Neutralizes any wrapper-tag substrings a Phase 2 reminder might carry, so a reminder cannot
 * close INJECTION's `<memory_context>` wrapper early and let the rest of its text escape the
 * "observations, not directives" framing the wrapper provides. Only the delivered copy is
 * touched — the step record keeps the model's text as written, since that is what the
 * measurement reads. Case-insensitive and whitespace-tolerant (`</MEMORY_CONTEXT>`,
 * `</memory_context >`, `< /memory_context>`, a line break before `>`) — an exact, case-sensitive
 * match on the two literal tags left every one of those variants unneutralized. */
export function escapeInjectionTags(reminder: string): string {
	return reminder.replace(/<\s*\/?\s*memory_context\b[^>]*>/gi, tag => tag.replace(/[<>]/g, ""));
}

/** `text` cut to at most `max` bytes, head 3/4 tail 1/4, with an elision line between. */
function capBytes(input: string, max: number): string {
	const buf = Buffer.from(input, "utf8");
	if (buf.length <= max) return input;
	const headBytes = Math.floor((max * 3) / 4);
	const tailBytes = max - headBytes;
	const elided = buf.length - headBytes - tailBytes;
	const head = buf.subarray(0, headBytes).toString("utf8");
	const tail = buf.subarray(buf.length - tailBytes).toString("utf8");
	return `${head}\n…[${elided} bytes elided]…\n${tail}`;
}

function toolCallArgs(args: Record<string, unknown>): string {
	const json = JSON.stringify(args);
	return json.length > ARGS_CAP_CHARS ? json.slice(0, ARGS_CAP_CHARS) + "…" : json;
}

function renderNotice(m: Msg): string | undefined {
	if (m.role === "user") {
		if (m.steering === true) return `User Message (steer): ${capBytes(text(m), TEXT_CAP_BYTES)}`;
		return `User Message: ${capBytes(text(m), TEXT_CAP_BYTES)}`;
	}
	if (m.role === "developer") return `System Notice: ${capBytes(text(m), TEXT_CAP_BYTES)}`;
	if (m.role === "custom" || m.role === "hookMessage") return `System Notice: ${capBytes(text(m), TEXT_CAP_BYTES)}`;
	if (m.role === "fileMention") return `System Notice: @-mentioned files: ${(m.files ?? []).map(f => f.path).join(", ")}`;
	if (m.role === "compactionSummary") return `System Notice: [compaction summary] ${capBytes(m.summary ?? "", TEXT_CAP_BYTES)}`;
	if (m.role === "branchSummary") return `System Notice: [branch summary] ${capBytes(m.summary ?? "", TEXT_CAP_BYTES)}`;
	if (m.role === "bashExecution" || m.role === "pythonExecution") {
		if (m.excludeFromContext) return undefined;
		const cmd = m.command ?? m.code ?? "";
		return `System Notice: Ran \`${cmd}\`\n${capBytes(m.output ?? "", TEXT_CAP_BYTES)}`;
	}
	return undefined;
}

interface Turn {
	before: Msg[];
	assistant: Msg;
	results: Msg[];
}

/** Groups a message window into turns — the messages before each assistant message, the assistant
 * message itself, and the toolResult messages that follow it — plus whatever trails after the
 * last turn's results (not yet answered by an assistant message). Doing this once, up front,
 * removes the index-cursor bookkeeping renderObservation used to need. */
function splitTurns(windowMessages: readonly Msg[]): { turns: Turn[]; tail: Msg[] } {
	const turns: Turn[] = [];
	let before: Msg[] = [];
	let i = 0;
	while (i < windowMessages.length) {
		const m = windowMessages[i];
		if (m.role === "assistant") {
			const assistant = m;
			i++;
			const results: Msg[] = [];
			while (i < windowMessages.length && windowMessages[i].role === "toolResult") {
				results.push(windowMessages[i]);
				i++;
			}
			turns.push({ before, assistant, results });
			before = [];
		} else {
			before.push(m);
			i++;
		}
	}
	return { turns, tail: before };
}

function renderToolResult(m: Msg): string {
	const rendered = Array.isArray(m.content) ? m.content.map(b => (b.type === "text" ? (b.text ?? "") : "[image]")).join("\n") : (m.content ?? "");
	return capBytes(rendered, RESULT_CAP_BYTES);
}

/** Renders the [Task Description] / [Recent Trajectory] / [New Input] observation Phase 1 and
 * Phase 2 read. The window is the last `min(WINDOW_TURNS, runStep - 1)` assistant turns of
 * `messages` — a COUNT, not a located boundary message: `runStep - 1` is exactly how many turns
 * have happened since this run started (by construction: `runStep = turn - runStartTurn + 1`), so
 * taking that many turns from the tail of the full split is the run's own window with no lookup
 * at all. This is what fixes F7/W1: a stored message reference breaks the moment the fork clones
 * `messages` for the next call (`runner.ts:1906-1914`), a turn count does not. `tailCount`, when
 * given, is the number of trailing messages (from the SAME `messages` this call received) that
 * express the run's own opening text — passed only on the call that classified a `prompt`, so that
 * text isn't echoed back a second time as `[New Input]`; it needs no cross-call memory either. */
export function renderObservation(taskDescription: string, messages: readonly Msg[], runStep: number, tailCount?: number): string {
	const parts: string[] = [`[Task Description]\n${taskDescription}`];

	const windowMessages = tailCount ? messages.slice(0, messages.length - tailCount) : messages;
	const { turns: allTurns, tail } = splitTurns(windowMessages);
	// However many turns this run has actually had so far (runStep - 1), capped at WINDOW_TURNS and
	// at however many turns `messages` actually still holds (a mid-run compaction can drop some).
	// Never negative: `runStep` is always >= 1, so the floor is 0 (the run's own first step).
	const shown = Math.max(0, Math.min(WINDOW_TURNS, runStep - 1, allTurns.length));
	const shownTurns = shown > 0 ? allTurns.slice(-shown) : [];

	if (shownTurns.length > 0) {
		// The source formats in the real count here (memory_enabled_agent.py:163); an earlier version
		// of this file left the plan's `k` placeholder in literally, rendering "(last k steps)".
		parts.push(`[Recent Trajectory (last ${shown} steps)]`);
		shownTurns.forEach((turn, k) => {
			const n = runStep - shown + k;
			const lines: string[] = [`[Step ${n}]`];
			for (const m of turn.before) {
				const notice = renderNotice(m);
				if (notice !== undefined) lines.push(notice);
			}
			lines.push(`Agent Analysis: ${text(turn.assistant)}`);
			const calls = toolCallsOf(turn.assistant);
			if (calls.length > 0) {
				const shownCalls = calls.slice(0, COMMANDS_SHOWN);
				const rendered = shownCalls.map(c => `${c.name}(${toolCallArgs(c.arguments)})`).join("; ");
				const more = calls.length > COMMANDS_SHOWN ? ` (+${calls.length - COMMANDS_SHOWN} more)` : "";
				lines.push(`Commands Executed: ${rendered}${more}`);
			}
			if (turn.results.length > 0) lines.push(`Terminal Output: ${turn.results.map(renderToolResult).join("\n---\n")}`);
			parts.push(lines.join("\n"));
		});
		const trailingNotices: string[] = [];
		for (const m of tail) {
			const notice = renderNotice(m);
			if (notice !== undefined) trailingNotices.push(notice);
		}
		if (trailingNotices.length > 0) parts.push(`[New Input]\n${trailingNotices.join("\n")}`);
	}

	return parts.join("\n\n");
}

export interface CompleteRequest {
	ctx: unknown;
	system: string;
	user: string;
	tools?: readonly ToolSpec[];
	maxTokens: number;
	signal: AbortSignal;
	sessionId: string;
	model: unknown;
}
export interface Usage {
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
}
export interface Completion {
	text: string;
	toolCalls: { name: string; arguments: Record<string, unknown> }[];
	usage: Usage;
	error?: string;
}
export interface Deps {
	complete: (request: CompleteRequest) => Promise<Completion>;
	now: () => number;
	newId: () => string;
	env: Record<string, string | undefined>;
}

export interface StepRecord {
	turn: number;
	runStep: number;
	key: string;
	trigger: "prompt" | "steer" | "continuation";
	taskDescription?: string;
	model?: string;
	phase1?: { operations: Operation[]; latencyMs: number; usage: Usage; error?: string };
	phase2?: { intervention: string | null; latencyMs: number; usage: Usage; error?: string };
	observation?: string;
	deadline: boolean;
	skipped?: "no-model" | "legion";
	error?: string;
	latencyMs?: number;
	/** Every id the bank held when the step started — the audit population. */
	bankBefore: { knowledge: string[]; procedural: string[] };
	/** The ids actually rendered into the PHASE 1 prompt (the bank as it stood before Phase 1's own
	 * edits): what the memory agent was shown when it decided what to save, update or delete.
	 * Equal to bankBefore unless the bank exceeded MAX_MEMORY_IN_PROMPT entries, in which case this
	 * holds only the BM25 top-PREFILTER_TOP_K per section (see bankShownIds/formatBank). Phase 2
	 * renders the bank AGAIN after Phase 1's edits apply, so a grounding check against an entry
	 * Phase 1 itself just saved needs bankAfter's presence, not this field — bankShown captures
	 * only the one view this step's bank had in common between the two calls (F9: this field did
	 * not previously say which of the two prompts it recorded). A grounding check against a PRE-
	 * EXISTING constraint ("this id was in what the model saw") must read this field, not
	 * bankBefore. */
	bankShown: { knowledge: string[]; procedural: string[] };
	bankAfter: { knowledge: number; procedural: number; statusChars: number };
}
/** Appended by the before_provider_request handler each time an outgoing payload carries the
 * reminder's RENDERED `<memory_context>` text (not the bare reminder) while the reminder is
 * armed. A reminder is armed from the moment Phase 2 chooses it until a later `context` call with
 * a different fingerprint disarms it (Deviation 7-adjacent): a stale reminder can never match a
 * later request's payload, and a reminder that quotes the user's own words verbatim cannot
 * false-match transcript history it quotes, because only the rendered wrapper counts. `attempts`
 * counts re-sends of the same request (a provider retry). A step with an intervention and no
 * delivery record = the reminder never reached the wire. */
export interface DeliveryRecord {
	turn: number;
	key: string;
	delivered: true;
	attempts: number;
}

/** Minimal structural subset of the fork's ExtensionAPI the core needs; the entry binds the real one. */
export interface Pi {
	on: (event: string, handler: (event: never, ctx: never) => unknown) => void;
	appendEntry: (type: string, data: unknown) => void;
	logger: { warn: (msg: string, meta?: unknown) => void; debug: (msg: string, meta?: unknown) => void };
}

export default function createProactiveMemory(deps: Deps) {
	return function proactiveMemory(pi: Pi) {
		const state = {
			bank: emptyBank(),
			lastMemoryTurn: 0,
			lastKey: undefined as string | undefined,
			taskDescription: "",
			runStartTurn: 1,
			resetBoundaryId: undefined as string | undefined,
			compactionId: undefined as string | undefined,
			loaded: false,
			warnedNoModel: false,
			generation: 0,
		};
		let inflight: { controller: AbortController; settled: Promise<void> } | undefined;
		let lastReminder: { key: string; turn: number; message: Msg; rendered: string; attempts: number; armed: boolean } | undefined;
		const legion = LEGION_ENV.some(name => deps.env[name] !== undefined && deps.env[name] !== "");

		for (const event of ["session_start", "session_switch", "session_branch", "session_tree"] as const) {
			pi.on(event, () => {
				state.generation++;
				state.loaded = false;
				lastReminder = undefined;
				inflight?.controller.abort("session changed");
			});
		}

		pi.on("before_provider_request", (event: { payload: unknown }) => {
			if (!lastReminder?.armed) return;
			if (!JSON.stringify(event.payload).includes(JSON.stringify(lastReminder.rendered).slice(1, -1))) return;
			lastReminder.attempts++;
			pi.appendEntry(DELIVERY_ENTRY_TYPE, { turn: lastReminder.turn, key: lastReminder.key, delivered: true, attempts: lastReminder.attempts } satisfies DeliveryRecord);
		});

		pi.on(
			"context",
			async (
				event: { messages: readonly Msg[] },
				ctx: {
					agent: { kind: string };
					sessionManager: { getBranch: () => readonly BranchEntry[]; getSessionId: () => string };
					models: { resolve: (role: string) => { provider: string; id: string } | undefined };
				},
			) => {
				try {
					if (ctx.agent.kind !== "main") return;
					const kind = classifyRequest(event.messages);
					if (kind.kind === "side") return;
					const branch = ctx.sessionManager.getBranch();
					const boundary = latestResetBoundaryId(branch);
					if (!state.loaded || boundary !== state.resetBoundaryId) {
						Object.assign(state, loadState(branch), { loaded: true, resetBoundaryId: boundary });
					}
					const turn = countTurns(branch);
					const key = fingerprint(event.messages);
					const compactionId = latestCompactionId(branch);
					if (kind.kind === "prompt") {
						state.taskDescription = kind.text;
						state.runStartTurn = turn;
					}
					if (key === state.lastKey && lastReminder?.key === key) return { messages: [...event.messages, lastReminder.message] };
					if (lastReminder && lastReminder.key !== key) lastReminder.armed = false;
					if (!shouldRun(kind, key, state.lastKey, turn, state.lastMemoryTurn, compactionId !== state.compactionId)) return;
					state.compactionId = compactionId;
					if (inflight) {
						inflight.controller.abort("superseded");
						await inflight.settled;
					}
					const generation = state.generation;
					const controller = new AbortController();
					const { promise: settled, resolve: settle } = Promise.withResolvers<void>();
					inflight = { controller, settled };
					// Everything from here on runs inside this try, whose finally unconditionally settles
					// `inflight` — so a throw building `record` itself (a malformed persisted bank, say)
					// still releases the next context call instead of blocking it for STEP_DEADLINE_MS or
					// forever (the fail-open contract is only as good as this settling being unconditional).
					try {
						state.lastMemoryTurn = turn;
						state.lastKey = key;
						const started = deps.now();
						const runStep = turn - state.runStartTurn + 1;
						const record: StepRecord = {
							turn,
							runStep,
							key,
							trigger: kind.kind,
							taskDescription: kind.kind === "continuation" ? undefined : state.taskDescription,
							deadline: false,
							bankBefore: ids(state.bank),
							bankShown: { knowledge: [], procedural: [] },
							bankAfter: counts(state.bank),
						};
						let result: { messages: Msg[] } | undefined;
						let timer: ReturnType<typeof setTimeout> | undefined;
						try {
							if (legion) {
								record.skipped = "legion";
								return result;
							}
							const model = ctx.models.resolve(MEMORY_ROLE);
							if (!model) {
								record.skipped = "no-model";
								if (!state.warnedNoModel) {
									pi.logger.warn("proactive-memory: role " + MEMORY_ROLE + " resolves to no model; no memory step runs in this session");
									state.warnedNoModel = true;
								}
								return result;
							}
							record.model = model.provider + "/" + model.id;
							timer = setTimeout(() => controller.abort("deadline"), STEP_DEADLINE_MS);
							try {
								const observation = renderObservation(state.taskDescription, event.messages, runStep, kind.kind === "prompt" ? kind.tailCount : undefined);
								record.bankShown = bankShownIds(state.bank, observation);
								const p1 = await deps.complete({
									ctx,
									system: PHASE1_SYSTEM,
									user: render(PHASE1_USER, { step: String(runStep), bank: formatBank(state.bank, observation), observation }),
									tools: BANK_TOOL_SPECS,
									maxTokens: PHASE1_MAX_TOKENS,
									signal: controller.signal,
									sessionId: ctx.sessionManager.getSessionId(),
									model,
								});
								if (state.generation !== generation) return result;
								const applied = applyOperations(state.bank, p1.error ? [] : p1.toolCalls, () => new Date(deps.now()).toISOString(), deps.newId);
								state.bank = { ...applied.bank, turn };
								record.phase1 = { operations: applied.operations, latencyMs: deps.now() - started, usage: p1.usage, error: p1.error };
								if (applied.changed) pi.appendEntry(BANK_ENTRY_TYPE, state.bank);
								if (controller.signal.aborted) {
									record.deadline = controller.signal.reason === "deadline";
									if (controller.signal.reason === "superseded") record.error = "superseded";
									return result;
								}
								if (STEP_DEADLINE_MS - (deps.now() - started) < PHASE2_MIN_REMAINING_MS) {
									record.deadline = true;
									return result;
								}
								const p2Started = deps.now();
								const p2 = await deps.complete({
									ctx,
									system: PHASE2_SYSTEM,
									user: render(PHASE2_USER, { step: String(runStep), bank: formatBank(state.bank, observation), observation }),
									maxTokens: PHASE2_MAX_TOKENS,
									signal: controller.signal,
									sessionId: ctx.sessionManager.getSessionId(),
									model,
								});
								const intervention = p2.error ? null : parsePhase2(p2.text);
								record.phase2 = { intervention, latencyMs: deps.now() - p2Started, usage: p2.usage, error: p2.error };
								if (controller.signal.aborted) {
									record.deadline = controller.signal.reason === "deadline";
									if (controller.signal.reason === "superseded") record.error = "superseded";
									return result;
								}
								if (intervention === null) return result;
								record.observation = observation;
								const rendered = render(INJECTION, { reminder: escapeInjectionTags(intervention) });
								const developer: Msg = { role: "developer", content: [{ type: "text", text: rendered }], attribution: "agent", timestamp: deps.now() };
								lastReminder = { key, turn, message: developer, rendered, attempts: 0, armed: true };
								result = { messages: [...event.messages, developer] };
								return result;
							} finally {
								clearTimeout(timer);
							}
						} catch (error) {
							record.error = message(error);
							return result;
						} finally {
							try {
								if (state.generation === generation) {
									record.bankAfter = counts(state.bank);
									record.latencyMs = deps.now() - started;
									pi.appendEntry(STEP_ENTRY_TYPE, record);
								} else pi.logger.debug("proactive-memory: step orphaned by a session change; nothing written", { turn });
							} catch (error) {
								pi.logger.warn("proactive-memory: record failed", { error: message(error) });
							}
						}
					} finally {
						if (inflight?.controller === controller) inflight = undefined;
						settle();
					}
				} catch (error) {
					pi.logger.warn("proactive-memory: handler failed", { error: message(error) });
					return undefined;
				}
			},
		);
	};
}
