// AskGate: before a root session's scoped Dispatch write executes, one model call judges it
// against the AskGate charter and answers `allow` or `revise`. `askgate.ts` is the entry that
// binds the fork's completion call; everything else lives here so the tests drive it through a
// fake `pi`, a fake `ctx` and a stub completion.
//
// Scope: `write xd://<device>` calls, seen on the nested `tool_call` event the device wrapper
// emits with `toolName = <device>` and the validated arguments, for dispatch_ask,
// dispatch_message, dispatch_edit_ask, dispatch_comment, dispatch_doc_edit, and dispatch_issue
// when its arguments carry `spec`. Top-level sessions only (`ctx.agent.kind === "main"`); the
// factory is rebound in every subagent, which this check keeps out.
//
// OMP_ASKGATE, read when the extension binds to a session: `off` registers nothing; `warn`
// (unset) runs a revised call and hands the agent the reason as context after the call's
// result; `block` refuses a revised call with the reason as the `write`'s error result. Any
// other value throws at bind. OMP_ASKGATE_TIMEOUT_MS replaces the 90 s deadline.
//
// Per gated call, in order:
//   - killed: `advisor.disableRoster` in local-overrides.yml (under PI_CODING_AGENT_DIR, else
//     ~/.omp/agent) lists `askgate`. The file is read on every call, so a kill reaches running
//     sessions; an unparsable file is not a kill.
//   - rebuttal: the agent put `"advisor_rebuttal": "<one line>"` in the device JSON. The key is
//     stripped on the outer `write` event (before the device validates its arguments) and the
//     call runs unasked. At most 64 unspent rebuttals are remembered.
//   - halted: three consecutive failures to reach a verdict (no model, timeout, error, no
//     parsable answer) stop the gate for the session, with one UI notice.
//   - breaker: two revises on one target (the device plus its issue/ask/artifact/project
//     values) send the third attempt unasked; an allow, a rebuttal or a trip resets the target.
//   - verdict: `@askgate` (modelRoles.askgate) is asked once, raced against the deadline, with
//     the charter after the primary's system prompt as system text, and the newest 256 KiB of
//     the transcript plus askgate-request.md as the user message. The last line of the answer
//     must be {"decision":"allow"} or {"decision":"revise","reason":"…"}.
// Every outcome other than an unscoped or subagent call appends one `advisor-gate` entry
// (pi.appendEntry, never sent to the model), which scripts/advisor-report reads. Every path
// fails open: a throw inside the handler is recorded as `error` and the call runs, so the
// runner's fail-closed conversion is never reached. OMP_ASKGATE_DUMP names a file each gate's
// system and user prompts are appended to.
import { createHash } from "node:crypto";
import * as path from "node:path";
import requestTemplate from "./askgate-request.md" with { type: "text" };

export const SCOPED_DEVICES = ["dispatch_ask", "dispatch_message", "dispatch_edit_ask", "dispatch_comment", "dispatch_doc_edit", "dispatch_issue"] as const;
export const SPEC_ONLY_DEVICE = "dispatch_issue";
export const BREAKER_KEYS = ["issue", "ask", "artifact", "project"] as const;
export const REBUTTAL_KEY = "advisor_rebuttal";
export const GATE_TIMEOUT_MS = 90_000;
export const GATE_CONTEXT_MAX_BYTES = 256 * 1024;
export const GATE_ARGS_MAX_BYTES = 64 * 1024;
export const GATE_BREAKER_REVISES = 2;
export const GATE_HALT_AFTER_FAILURES = 3;
export const ADVISOR_GATE_ENTRY_TYPE = "advisor-gate";
export const MODEL_ROLE = "@askgate";
export const GATE_EFFORT = "high";
const SLUG = "askgate";
const REBUTTAL_CAP = 64;
const MODES = ["off", "warn", "block"] as const;
const XD_PATH = /^xd:\/\/([a-z_]+)$/;
// Room kept for a clip marker (` … [elided N bytes]`, at most 32 bytes) and the window's elision line.
const CLIP_MARKER_BYTES = 32;
const WINDOW_MARKER_BYTES = 64;
const SEPARATOR = "\n\n";

export type Mode = (typeof MODES)[number];
type Input = Record<string, unknown>;
export type Usage = { input: number; output: number; cacheRead: number; cacheWrite: number; cost: number };
export interface GateEntry {
	advisor: "AskGate";
	tool: string;
	path: string;
	toolCallId: string;
	decision: "allow" | "revise";
	outcome: "verdict" | "rebuttal" | "breaker" | "timeout" | "error" | "no-verdict" | "unavailable" | "halted" | "killed";
	verdictMode: "warn" | "block";
	reason?: string;
	rebuttal?: string;
	/** The answer's tail when it held no verdict. */
	raw?: string;
	latencyMs: number;
	revisesForKey: number;
	argsDigest: string;
	promptBytes?: number;
	/** `provider/id` that `@askgate` resolved to, on every entry that reached the model call. */
	model?: string;
	usage?: Usage;
}
export interface CompleteRequest { ctx: unknown; model: unknown; system: string; user: string; sessionId: string; signal: AbortSignal }
export type Completion = { text: string; error?: string; usage?: Usage };
export interface Deps {
	env: Record<string, string | undefined>;
	home: string;
	now: () => number;
	/** File text, or undefined when the file does not exist. */
	readFile: (p: string) => string | undefined;
	/** Appends to a file; serves OMP_ASKGATE_DUMP. */
	appendFile: (p: string, text: string) => void;
	complete: (req: CompleteRequest) => Promise<Completion>;
	charterPath: string;
}
// Handlers of any event shape register here (`never` parameters accept every typed handler), so the
// fork's ExtensionAPI is assignable to it.
export interface Pi {
	on(event: string, handler: (event: never, ctx: never) => unknown): void;
	appendEntry(customType: string, data?: unknown): void;
	logger: { debug(message: string, context?: Record<string, unknown>): void };
}
type ToolCallEvent = { toolName: string; toolCallId: string; input: Input };
type ToolCallResult = { block?: boolean; reason?: string; input?: Input; additionalContext?: string } | undefined;
type GateCtx = {
	agent: { kind: string };
	hasUI: boolean;
	ui: { notify(message: string, type?: "info" | "warning" | "error"): void };
	getSystemPrompt(): string[];
	models: { resolve(spec: string): { provider: string; id: string } | undefined };
	sessionManager: { getBranch(): readonly Entry[]; getSessionId(): string };
};

// The session entry shapes (fork session-entries.ts); getBranch() returns every type raw.
export type Entry =
	| { type: "message"; id: string; timestamp: string; message: { role: string; content?: unknown; toolName?: string; isError?: boolean; command?: string; code?: string; output?: string; excludeFromContext?: boolean } }
	| { type: "custom_message"; id: string; timestamp: string; customType: string; content: string | Array<{ type: string; text?: string }>; display: boolean }
	| { type: "custom"; id: string; timestamp: string; customType: string; data?: unknown }
	| { type: "compaction"; id: string; timestamp: string; summary: string; firstKeptEntryId: string }
	| { type: "branch_summary"; id: string; timestamp: string; summary: string }
	| { type: "reset_boundary"; id: string; timestamp: string }
	| { type: string; id?: string; timestamp?: string };

/** The module's one object guard: transcript and overlay data are untyped JSON/YAML. */
export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const byteLength = (text: string) => Buffer.byteLength(text, "utf8");

export function parseMode(raw: string | undefined): Mode {
	const value = (raw ?? "warn").trim();
	if ((MODES as readonly string[]).includes(value)) return value as Mode;
	throw new Error(`OMP_ASKGATE must be one of ${MODES.join("|")}, got ${JSON.stringify(raw)}`);
}

function parseTimeout(raw: string | undefined): number {
	if (raw === undefined) return GATE_TIMEOUT_MS;
	if (/^[1-9]\d*$/.test(raw.trim())) return Number(raw.trim());
	throw new Error(`OMP_ASKGATE_TIMEOUT_MS must be a positive integer, got ${JSON.stringify(raw)}`);
}

export function overlayPath(env: Record<string, string | undefined>, home: string): string {
	return path.join(env.PI_CODING_AGENT_DIR || path.join(home, ".omp", "agent"), "local-overrides.yml");
}

/** Whether the overlay lists `slug` under `advisor.disableRoster`. A missing file is no kill; an unparsable one is no kill, reported to `onUnparsable`. */
export function isKilled(overlayText: string | undefined, slug: string, onUnparsable?: (error: unknown) => void): boolean {
	if (overlayText === undefined) return false;
	let doc: unknown;
	try {
		doc = Bun.YAML.parse(overlayText);
	} catch (error) {
		onUnparsable?.(error);
		return false;
	}
	const roster = isRecord(doc) && isRecord(doc.advisor) ? doc.advisor.disableRoster : undefined;
	return Array.isArray(roster) && roster.some(member => typeof member === "string" && member.trim().toLowerCase() === slug);
}

export function scopedDevice(toolName: string, input: Input): boolean {
	return (SCOPED_DEVICES as readonly string[]).includes(toolName) && (toolName !== SPEC_ONLY_DEVICE || input.spec !== undefined);
}

export function breakerKey(toolName: string, input: Input): string {
	let key = toolName;
	for (const k of BREAKER_KEYS) if (input[k] !== undefined) key += `\u0000${k}=${String(input[k])}`;
	return key;
}

/** Splits a top-level REBUTTAL_KEY off a device's JSON arguments; anything else comes back unchanged. */
export function extractRebuttal(content: string): { rebuttal?: string; content: string } {
	let parsed: unknown;
	try {
		parsed = JSON.parse(content);
	} catch {
		return { content };
	}
	if (!isRecord(parsed) || !(REBUTTAL_KEY in parsed)) return { content };
	const { [REBUTTAL_KEY]: value, ...rest } = parsed;
	if (typeof value !== "string" || !value.trim()) return { content };
	return { rebuttal: value.trim(), content: JSON.stringify(rest) };
}

/** `text` cut to at most `max` UTF-8 bytes, marker included. */
function clipBytes(text: string, max: number): string {
	const size = byteLength(text);
	if (size <= max) return text;
	// A cut inside a multi-byte character decodes to U+FFFD; drop it.
	const head = Buffer.from(text, "utf8").subarray(0, Math.max(0, max - CLIP_MARKER_BYTES)).toString("utf8").replace(/\uFFFD$/, "");
	return `${head} … [elided ${size - byteLength(head)} bytes]`;
}

export function renderArgs(input: Input): string {
	return clipBytes(JSON.stringify(input, null, 2), GATE_ARGS_MAX_BYTES);
}

function textOf(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	const parts: string[] = [];
	for (const part of content) {
		if (typeof part === "string") parts.push(part);
		else if (isRecord(part) && part.type === "text" && typeof part.text === "string") parts.push(part.text);
		else if (isRecord(part) && part.type === "image") parts.push("[image]");
	}
	return parts.join("\n");
}

const escapeXml = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function renderAssistant(content: unknown): string {
	const parts: string[] = [];
	for (const block of Array.isArray(content) ? content : []) {
		if (!isRecord(block)) continue;
		if (block.type === "text" && typeof block.text === "string" && block.text.trim()) parts.push(block.text);
		else if (block.type === "thinking" && typeof block.thinking === "string" && block.thinking.trim()) parts.push(`(thinking) ${clipBytes(block.thinking, 1024)}`);
		else if (block.type === "toolCall") {
			const args = typeof block.arguments === "string" ? block.arguments : JSON.stringify(block.arguments ?? {});
			parts.push(`→ ${String(block.name)}(${clipBytes(args, 2048)})`);
		}
	}
	return parts.length > 0 ? `**agent**: ${parts.join("\n")}` : "";
}

function renderMessage(message: Record<string, unknown>): string {
	switch (message.role) {
		case "user":
			return `**user**: ${textOf(message.content)}`;
		case "assistant":
			return renderAssistant(message.content);
		case "toolResult":
			return `⇒ ${String(message.toolName)}${message.isError ? " (error)" : ""}: ${clipBytes(textOf(message.content), 8 * 1024)}`;
		case "bashExecution":
			return message.excludeFromContext ? "" : `→ user-bash ${String(message.command)} ⇒ ${clipBytes(String(message.output ?? ""), 8 * 1024)}`;
		case "pythonExecution":
			return message.excludeFromContext ? "" : `→ user-python ${String(message.code)} ⇒ ${clipBytes(String(message.output ?? ""), 8 * 1024)}`;
		case "developer":
			return `**system**: ${clipBytes(textOf(message.content), 2 * 1024)}`;
		default:
			return "";
	}
}

function renderEntry(entry: Record<string, unknown>): string {
	switch (entry.type) {
		case "message":
			return isRecord(entry.message) ? renderMessage(entry.message) : "";
		case "custom_message": {
			const text = textOf(entry.content);
			if (entry.customType === "envoy-message") return `<primary-message kind="envoy-message">\n${escapeXml(text)}\n</primary-message>`;
			if (entry.display === false && entry.customType !== "advisor") return "";
			return `[${String(entry.customType)}] ${text.replace(/\s+/g, " ").trim().slice(0, 120)}`;
		}
		case "compaction":
			return `[compaction] ${clipBytes(String(entry.summary ?? ""), 8 * 1024)}`;
		case "branch_summary":
			return `[branch] ${clipBytes(String(entry.summary ?? ""), 2 * 1024)}`;
		default:
			return "";
	}
}

/** The entries the primary's context still holds: after the last `/clear`, and from the newest compaction's first kept entry, its summary in their place. */
function contextEntries(entries: readonly Entry[]): Entry[] {
	let start = 0;
	for (let i = entries.length - 1; i >= 0; i--) {
		if (entries[i].type === "reset_boundary") {
			start = i + 1;
			break;
		}
	}
	const scoped = entries.slice(start);
	let at = -1;
	for (let i = scoped.length - 1; i >= 0; i--) {
		if (scoped[i].type === "compaction") {
			at = i;
			break;
		}
	}
	if (at < 0) return scoped;
	const compaction = scoped[at] as Record<string, unknown>;
	const kept = scoped.findIndex(e => e.id === compaction.firstKeptEntryId);
	return [scoped[at], ...scoped.slice(kept < 0 ? at + 1 : kept).filter(e => e.type !== "compaction")];
}

/** The newest whole entries of the primary's context that fit in `capBytes`, oldest first, behind an elision line when older ones were dropped. */
export function renderTranscript(entries: readonly Entry[], capBytes: number): string {
	const rendered = contextEntries(entries)
		.map(e => (isRecord(e) ? renderEntry(e) : ""))
		.filter(text => text !== "");
	const sizes = rendered.map(byteLength);
	// Bytes of rendered[from..to) joined by SEPARATOR.
	const span = (from: number, to: number) => sizes.slice(from, to).reduce((sum, size) => sum + size, 0) + SEPARATOR.length * Math.max(0, to - from - 1);
	if (span(0, rendered.length) <= capBytes) return rendered.join(SEPARATOR);
	const budget = capBytes - WINDOW_MARKER_BYTES;
	let first = rendered.length;
	for (let used = 0; first > 0; first--) {
		const next = used + sizes[first - 1] + (first < rendered.length ? SEPARATOR.length : 0);
		if (next > budget) break;
		used = next;
	}
	if (first === rendered.length) {
		// The newest entry alone overflows the window: keep its head.
		rendered[rendered.length - 1] = clipBytes(rendered[rendered.length - 1], budget);
		first = rendered.length - 1;
	}
	return `… [elided ${span(0, first)} bytes of earlier transcript]${SEPARATOR}${rendered.slice(first).join(SEPARATOR)}`;
}

/** The charter text with each whole-line `@<path>` import (`~/` is `home`, else relative to the charter) replaced by that file, one level deep; a missing import stays as written. */
export function loadCharter(readFile: (p: string) => string | undefined, charterPath: string, home: string): string {
	const text = readFile(charterPath);
	if (text === undefined) throw new Error(`AskGate charter not found at ${charterPath}`);
	return text
		.split("\n")
		.map(line => {
			const match = /^@(\S+)$/.exec(line.trim());
			if (!match) return line;
			const target = match[1].startsWith("~/") ? path.join(home, match[1].slice(2)) : path.resolve(path.dirname(charterPath), match[1]);
			return readFile(target) ?? line;
		})
		.join("\n");
}

/** The verdict on the answer's last line (a closing code fence after it is ignored); a revise needs a non-empty reason. */
export function parseVerdict(text: string): { decision: "allow" | "revise"; reason?: string } | undefined {
	const lines = text.split("\n").map(line => line.trim()).filter(line => line !== "");
	while (lines.length > 0 && lines[lines.length - 1].startsWith("```")) lines.pop();
	let parsed: unknown;
	try {
		parsed = JSON.parse(lines.at(-1) ?? "");
	} catch {
		return undefined;
	}
	if (!isRecord(parsed)) return undefined;
	if (parsed.decision === "allow") return { decision: "allow" };
	if (parsed.decision === "revise" && typeof parsed.reason === "string" && parsed.reason.trim()) return { decision: "revise", reason: parsed.reason.trim() };
	return undefined;
}

/** Revise reasons per target since its last allowed call. */
export class Breaker {
	readonly #reasons = new Map<string, string[]>();
	revised(key: string, reason: string): void {
		const reasons = this.#reasons.get(key) ?? [];
		reasons.push(reason);
		this.#reasons.set(key, reasons);
	}
	tripped(key: string): boolean {
		return this.reasons(key).length >= GATE_BREAKER_REVISES;
	}
	reset(key: string): void {
		this.#reasons.delete(key);
	}
	reasons(key: string): readonly string[] {
		return this.#reasons.get(key) ?? [];
	}
}

function renderRequest(tool: string, input: Input, priorReasons: readonly string[]): string {
	const prior =
		priorReasons.length > 0
			? `You answered revise ${priorReasons.length} time(s) for this target since its last allowed call:\n${priorReasons.map(r => `- ${r}`).join("\n")}`
			: "";
	const values: Record<string, string> = { tool, args: renderArgs(input), priorReasons: prior };
	// One pass with a function replacer: argument text is never re-scanned or read as `$` patterns.
	return requestTemplate.replace(/\{\{(tool|args|priorReasons)\}\}/g, (_, name: string) => values[name]);
}

export const renderRevise = (reason: string) =>
	`AskGate did not send this call.\n${reason}\nSend the corrected call, or resend this one unchanged with "${REBUTTAL_KEY}": "<one line>" added to the JSON to override; an override is always sent and recorded.`;
export const renderWarn = (reason: string) =>
	`<advisor-gate advisor="AskGate" verdict="revise">\n${reason}\nThe call ran. Correct it now where the reason names a fix (edit the ask, retract it, or resend), or state your rebuttal in your next step.\n</advisor-gate>`;

export function createAskGate(deps: Deps): (pi: Pi) => void {
	return function askGate(pi: Pi): void {
		const mode = parseMode(deps.env.OMP_ASKGATE);
		if (mode === "off") return;
		const timeoutMs = parseTimeout(deps.env.OMP_ASKGATE_TIMEOUT_MS);
		const charter = loadCharter(deps.readFile, deps.charterPath, deps.home);
		const dumpPath = deps.env.OMP_ASKGATE_DUMP;
		// State lives in this binding: the fork rebinds the factory per session.
		const rebuttals = new Map<string, string>();
		const breaker = new Breaker();
		let failures = 0;
		let halted = false;

		const failed = (ctx: GateCtx) => {
			failures++;
			if (failures < GATE_HALT_AFTER_FAILURES || halted) return;
			halted = true;
			if (ctx.hasUI) ctx.ui.notify(`AskGate paused for this session after ${failures} consecutive calls without a verdict; Dispatch writes now go out unchecked.`, "warning");
		};

		/** The outer `write xd://<device>` event: strip a rebuttal before the device validates its arguments. */
		const stripRebuttal = (event: ToolCallEvent): ToolCallResult => {
			const { input } = event;
			const device = typeof input.path === "string" ? XD_PATH.exec(input.path)?.[1] : undefined;
			if (!device || !(SCOPED_DEVICES as readonly string[]).includes(device) || typeof input.content !== "string") return undefined;
			const { rebuttal, content } = extractRebuttal(input.content);
			if (rebuttal === undefined) return undefined;
			rebuttals.set(event.toolCallId, rebuttal);
			if (rebuttals.size > REBUTTAL_CAP) rebuttals.delete(rebuttals.keys().next().value as string);
			return { input: { ...input, content } };
		};

		type Base = Omit<GateEntry, "decision" | "outcome" | "latencyMs">;
		const gate = async (event: ToolCallEvent, ctx: GateCtx, started: number, base: Base, key: string, rebuttal: string | undefined): Promise<ToolCallResult> => {
			const record = (fields: Pick<GateEntry, "decision" | "outcome"> & Partial<GateEntry>) =>
				pi.appendEntry(ADVISOR_GATE_ENTRY_TYPE, { ...base, ...fields, latencyMs: deps.now() - started } satisfies GateEntry);
			const unparsable = (error: unknown) => pi.logger.debug("AskGate: local-overrides.yml does not parse; the gate stays on", { error: String(error) });
			if (isKilled(deps.readFile(overlayPath(deps.env, deps.home)), SLUG, unparsable)) {
				record({ decision: "allow", outcome: "killed" });
				return undefined;
			}
			if (rebuttal !== undefined) {
				breaker.reset(key);
				record({ decision: "allow", outcome: "rebuttal", rebuttal });
				return undefined;
			}
			if (halted) {
				record({ decision: "allow", outcome: "halted" });
				return undefined;
			}
			if (breaker.tripped(key)) {
				breaker.reset(key);
				record({ decision: "allow", outcome: "breaker" });
				return undefined;
			}

			const system = `<primary-system-prompt>\n${ctx.getSystemPrompt().join("\n\n")}\n</primary-system-prompt>\n\n${charter}`;
			const transcript = renderTranscript(ctx.sessionManager.getBranch(), GATE_CONTEXT_MAX_BYTES);
			const request = renderRequest(base.path, event.input, breaker.reasons(key));
			const user = transcript ? `${transcript}\n\n${request}` : request;
			const promptBytes = byteLength(system) + byteLength(user);
			if (dumpPath) deps.appendFile(dumpPath, `===== AskGate ${event.toolCallId} system =====\n${system}\n===== AskGate ${event.toolCallId} user =====\n${user}\n\n`);

			const model = ctx.models.resolve(MODEL_ROLE);
			if (!model) {
				failed(ctx);
				record({ decision: "allow", outcome: "unavailable" });
				return undefined;
			}
			const called = { promptBytes, model: `${model.provider}/${model.id}` };
			const controller = new AbortController();
			const { promise: expired, resolve: expire } = Promise.withResolvers<"timeout">();
			const timer = setTimeout(() => {
				controller.abort();
				expire("timeout");
			}, timeoutMs);
			let result: Completion | "timeout";
			try {
				// Promise.race subscribes to the completion, so a late rejection after the deadline is handled.
				result = await Promise.race([deps.complete({ ctx, model, system, user, sessionId: ctx.sessionManager.getSessionId(), signal: controller.signal }), expired]);
			} catch (error) {
				failed(ctx);
				record({ decision: "allow", outcome: "error", reason: error instanceof Error ? error.message : String(error), ...called });
				return undefined;
			} finally {
				clearTimeout(timer);
			}
			if (result === "timeout") {
				failed(ctx);
				record({ decision: "allow", outcome: "timeout", ...called });
				return undefined;
			}
			const usage = result.usage ? { usage: result.usage } : {};
			if (result.error !== undefined) {
				failed(ctx);
				record({ decision: "allow", outcome: "error", reason: result.error, ...called, ...usage });
				return undefined;
			}
			const verdict = parseVerdict(result.text);
			if (!verdict) {
				failed(ctx);
				record({ decision: "allow", outcome: "no-verdict", raw: result.text.slice(-2000), ...called, ...usage });
				return undefined;
			}
			failures = 0;
			if (verdict.decision === "allow" || verdict.reason === undefined) {
				breaker.reset(key);
				record({ decision: "allow", outcome: "verdict", ...called, ...usage });
				return undefined;
			}
			breaker.revised(key, verdict.reason);
			record({ decision: "revise", outcome: "verdict", reason: verdict.reason, revisesForKey: base.revisesForKey + 1, ...called, ...usage });
			return mode === "block" ? { block: true, reason: renderRevise(verdict.reason) } : { additionalContext: renderWarn(verdict.reason) };
		};

		pi.on("tool_call", async (event: ToolCallEvent, ctx: GateCtx): Promise<ToolCallResult> => {
			const started = deps.now();
			let base: Base | undefined;
			try {
				if (ctx.agent.kind !== "main") return undefined;
				if (event.toolName === "write") return stripRebuttal(event);
				// A remembered rebuttal is spent by its device event, whatever happens to the call.
				const rebuttal = rebuttals.get(event.toolCallId);
				rebuttals.delete(event.toolCallId);
				if (!scopedDevice(event.toolName, event.input)) return undefined;
				const key = breakerKey(event.toolName, event.input);
				base = {
					advisor: "AskGate",
					tool: event.toolName,
					path: `xd://${event.toolName}`,
					toolCallId: event.toolCallId,
					verdictMode: mode,
					revisesForKey: breaker.reasons(key).length,
					argsDigest: createHash("sha256").update(JSON.stringify(event.input)).digest("hex"),
				};
				return await gate(event, ctx, started, base, key, rebuttal);
			} catch (error) {
				// Fail open: the runner turns a thrown handler into a refusal.
				try {
					failed(ctx);
					pi.appendEntry(ADVISOR_GATE_ENTRY_TYPE, {
						advisor: "AskGate",
						tool: event.toolName,
						path: `xd://${event.toolName}`,
						toolCallId: event.toolCallId,
						verdictMode: mode,
						revisesForKey: 0,
						argsDigest: "",
						...base,
						decision: "allow",
						outcome: "error",
						reason: error instanceof Error ? error.message : String(error),
						latencyMs: deps.now() - started,
					} satisfies GateEntry);
				} catch {
					// The entry is best effort; the call still runs.
				}
				return undefined;
			}
		});
	};
}
