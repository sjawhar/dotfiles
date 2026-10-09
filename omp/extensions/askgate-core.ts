// AskGate: before a root session's scoped Dispatch write executes, one model call judges it
// against the AskGate charter and answers `allow` or `revise`. `askgate.ts` is the entry that
// binds the fork's completion call and buildSessionContext; everything else lives here so the
// tests drive it through a fake `pi`, a fake `ctx` and stubs.
//
// Scope: the same six calls on two surfaces, recorded under one tool name (dispatch_ask, …).
//   - A `bash` call whose whole command is one `dispatch` command (`dispatchCommandHead`, the
//     head rules of the plugin that ships the CLI, copied) naming `ask`, `message`, `edit-ask`,
//     `comment`, `doc-edit`, or `issue` with `--spec` or `--spec-file`. The gate learns the exact
//     arguments by running that command with `--dry-run` after the command name, in the call's
//     working directory and environment, its here-document still on stdin, under a 10 s limit,
//     and reviews the JSON it prints. A dry-run that fails, times out or prints no JSON object is
//     recorded as `error` and the call runs (the CLI refuses arguments it cannot parse, before it
//     sends anything). A command joined to anything else (`dispatch ask …; echo`) is not a
//     `dispatch` call under those rules and is not reviewed, nor is one carrying `--dry-run` or
//     `--help`, which sends nothing.
//   - `write xd://<device>` calls, seen on the nested `tool_call` event the device wrapper emits
//     with `toolName = <device>` and the validated arguments, for dispatch_ask, dispatch_message,
//     dispatch_edit_ask, dispatch_comment, dispatch_doc_edit, and dispatch_issue when its
//     arguments carry `spec`. Only sessions started before the `dispatch` CLI still have them.
// Top-level sessions only (`ctx.agent.kind === "main"`); the factory is rebound in every
// subagent, which this check keeps out.
//
// OMP_ASKGATE, read when the extension binds to a session: `off` registers nothing; `warn`
// (unset) runs a revised call and returns the reason as additional context, which the fork
// delivers only when the call succeeds and ran through the agent loop (a failing call, or a
// write made from eval, drops it); `block` refuses a revised call with the reason as the
// `write`'s error result. Any other value throws at bind.
//
// Deadline: OMP_ASKGATE_TIMEOUT_MS, or 90 s, clamped 5 s under the runner's own ceiling for a
// tool_call handler (extensionHandlers.toolCallTimeoutMs, 30 s unless configured) and raced from
// handler entry. A ceiling that leaves under 10 s binds nothing and logs why.
//
// Per gated call, in order:
//   - killed: `advisor.disableRoster` in local-overrides.yml (under PI_CODING_AGENT_DIR, else
//     ~/.omp/agent) lists `askgate`. The file is read on every call, so a kill reaches running
//     sessions; an unparsable file is not a kill.
//   - rebuttal: the agent put `"advisor_rebuttal": "<one line>"` in the device JSON, or
//     `--advisor-rebuttal '<one line>'` among a `dispatch` command's flags. The key is stripped
//     on the outer `write` event (before the device validates its arguments), the flag from the
//     `bash` command whatever the gate decides (the CLI would refuse it), and the call runs
//     unasked; the agent is told its rebuttal was recorded and removed before sending. At most
//     64 unspent device rebuttals are remembered.
//   - halted: three consecutive failures to reach a verdict (no model, timeout, error, no
//     parsable answer) stop the gate for the session, with one UI notice. The halt, the
//     count, the breaker and the rebuttals reset on session_switch and session_branch.
//   - breaker: two revises on one target (the device plus its issue/ask/artifact/project
//     values) send the third attempt unasked, and say so; the second revise already warns that
//     it will. An allow, a rebuttal or a trip resets the target.
//   - unavailable / skipped: no model for `@askgate`, or a session whose primary model is on
//     another provider than the gate's: nothing is rendered or sent, and the call runs.
//   - verdict: `@askgate` (modelRoles.askgate) is asked once, raced against the deadline. Its
//     system prompt is askgate-system.md and the charter, nothing else. The user message
//     (askgate-request.md) carries the agent under review as data: its system prompt and the
//     newest 256 KiB of its context (the fork's buildSessionContext), every piece XML-escaped
//     inside its own block, then the call. The last line of the answer must be
//     {"decision":"allow"} or {"decision":"revise","reason":"…"}. The reason reaches the agent
//     verbatim but for a tag-opening `<` (see `agentFacing`), capped at 2 KiB, and is recorded
//     as `deliveredReason`.
// Every outcome other than an unscoped or subagent call appends one `advisor-gate` entry
// (pi.appendEntry, never sent to the model) with the deadline it raced and every attempt's
// usage, the aborted one included. Every path fails open but
// one: a throw inside the handler is recorded as `error` and the call runs, and the deadline keeps
// the handler inside the runner's ceiling, whose expiry would refuse the call. The exception is a
// call given up without a verdict. When the call's tool ends first anyway (a user abort, or that
// ceiling), or the user stops the run while it waits (the path an eval-bridged write takes), the
// model call is cancelled and the entry reads `abandoned`. When the session shuts down while it
// waits, or a gated call arrives once shutdown has begun, the entry reads `shutdown`, written
// before the session is sealed. No verdict is not permission: block mode refuses either, warn mode
// lets it go, and neither counts toward the halt. OMP_ASKGATE_DUMP names a file each gate's system
// and user prompts are appended to, owner-only and never through a symlink.
import { createHash } from "node:crypto";
import * as path from "node:path";
import requestTemplate from "./askgate-request.md" with { type: "text" };
import systemTemplate from "./askgate-system.md" with { type: "text" };

const SCOPED_DEVICES = ["dispatch_ask", "dispatch_message", "dispatch_edit_ask", "dispatch_comment", "dispatch_doc_edit", "dispatch_issue"] as const;
const SPEC_ONLY_DEVICE = "dispatch_issue";
const SCOPED_COMMANDS = ["ask", "message", "edit-ask", "comment", "doc-edit", "issue"] as const;
const SPEC_ONLY_COMMAND = "issue";
const SPEC_FLAGS = ["--spec", "--spec-file"];
/** Flags under which a `dispatch` command sends nothing. */
const NO_SEND_FLAGS = ["--dry-run", "--help"];
const BREAKER_KEYS = ["issue", "ask", "artifact", "project", "in_reply_to"] as const;
const REBUTTAL_KEY = "advisor_rebuttal";
const REBUTTAL_FLAG = "--advisor-rebuttal";
export const DRY_RUN_TIMEOUT_MS = 10_000;
const GATE_TIMEOUT_MS = 90_000;
// The runner refuses a tool_call handler that outlives extensionHandlers.toolCallTimeoutMs, so the
// gate's deadline stays this far under it: room for the aborted call to report its usage
// (ABORT_GRACE_MS) and for the record. Below MIN_DEADLINE_MS a verdict is hopeless and the gate does not bind.
const CEILING_MARGIN_MS = 5_000;
const ABORT_GRACE_MS = 2_000;
const MIN_DEADLINE_MS = 10_000;
// The runner gives a session_shutdown handler 2 s (SESSION_SHUTDOWN_HANDLER_TIMEOUT_MS) and then seals
// the session, dropping any later entry: gates abandoned there get a shorter grace, and the handler
// waits at most SHUTDOWN_WAIT_MS for their records.
const SHUTDOWN_GRACE_MS = 1_000;
const SHUTDOWN_WAIT_MS = 1_500;
export const GATE_CONTEXT_MAX_BYTES = 256 * 1024;
const GATE_ARGS_MAX_BYTES = 64 * 1024;
const GATE_REASON_MAX_BYTES = 2 * 1024;
const GATE_BREAKER_REVISES = 2;
const GATE_HALT_AFTER_FAILURES = 3;
export const ADVISOR_GATE_ENTRY_TYPE = "advisor-gate";
const MODEL_ROLE = "@askgate";
export const GATE_EFFORT = "high";
const SLUG = "askgate";
const REBUTTAL_CAP = 64;
const MODES = ["off", "warn", "block"] as const;
const XD_PATH = /^xd:\/\/([a-z_]+)$/;
// Room kept for a clip marker (` … [elided N bytes]`, at most 32 bytes) and the window's elision line.
const CLIP_MARKER_BYTES = 32;
const WINDOW_MARKER_BYTES = 64;
const SEPARATOR = "\n\n";

type Mode = (typeof MODES)[number];
type Input = Record<string, unknown>;
export type Usage = { input: number; output: number; cacheRead: number; cacheWrite: number; cost: number };
export interface GateEntry {
	advisor: "AskGate";
	tool: string;
	path: string;
	toolCallId: string;
	/**
	 * `revise`: the gate did not pass the call as sent (a revise verdict, or in block mode a call given up
	 * without a verdict). A call was refused exactly when `verdictMode` is `block` and this is `revise`.
	 */
	decision: "allow" | "revise";
	outcome: "verdict" | "rebuttal" | "breaker" | "timeout" | "error" | "no-verdict" | "unavailable" | "halted" | "killed" | "skipped" | "abandoned" | "shutdown";
	verdictMode: Exclude<Mode, "off">;
	reason?: string;
	rebuttal?: string;
	/** On a revise: the reason exactly as the agent received it (the one agent-facing transform's output). */
	deliveredReason?: string;
	/** The answer's tail when it held no verdict. */
	raw?: string;
	latencyMs: number;
	revisesForKey: number;
	argsDigest: string;
	promptBytes?: number;
	/** `provider/id` that `@askgate` resolved to, on every entry that reached the model call. */
	model?: string;
	/** The deadline this call raced: OMP_ASKGATE_TIMEOUT_MS or 90 s, clamped under the runner's handler ceiling. */
	deadlineMs?: number;
	/** Every attempt's spend, the aborted one included. */
	usage?: Usage;
}
export interface CompleteRequest { ctx: unknown; model: unknown; system: string; user: string; sessionId: string; signal: AbortSignal }
export type Completion = { text: string; error?: string; usage?: Usage };
export interface DryRunOptions { cwd: string; env: Record<string, string | undefined>; timeoutMs: number; signal?: AbortSignal }
/** `exitCode` is null when the run was killed. */
export interface DryRunResult { exitCode: number | null; stdout: string; stderr: string; timedOut: boolean }
interface Deps {
	env: Record<string, string | undefined>;
	home: string;
	now: () => number;
	/** File text, or undefined when the file does not exist. */
	readFile: (p: string) => string | undefined;
	/** Appends to a file; serves OMP_ASKGATE_DUMP. */
	appendFile: (p: string, text: string) => void;
	complete: (req: CompleteRequest) => Promise<Completion>;
	charterPath: string;
	/** The primary's context as its model sees it: the fork's buildSessionContext over this session's branch. */
	contextMessages: (ctx: GateCtx) => readonly Message[];
	/** The runner's ceiling for a tool_call handler: extensionHandlers.toolCallTimeoutMs, read when asked. */
	handlerCeilingMs: () => number;
	/** Runs a `dispatch` command's --dry-run script under bash; `spawnDryRun` in the entry. */
	dryRun: (script: string, opts: DryRunOptions) => Promise<DryRunResult>;
}
// Handlers of any event shape register here (`never` parameters accept every typed handler), so the
// fork's ExtensionAPI is assignable to it.
interface Pi {
	on(event: string, handler: (event: never, ctx: never) => unknown): void;
	appendEntry(customType: string, data?: unknown): void;
	logger: { debug(message: string, context?: Record<string, unknown>): void; warn(message: string, context?: Record<string, unknown>): void };
}
type ToolCallEvent = { toolName: string; toolCallId: string; input: Input };
type ToolCallResult = { block?: boolean; reason?: string; input?: Input; additionalContext?: string } | undefined;
type GateCtx = {
	agent: { kind: string };
	hasUI: boolean;
	ui: { notify(message: string, type?: "info" | "warning" | "error"): void };
	getSystemPrompt(): string[];
	models: { resolve(spec: string): { provider: string; id: string } | undefined };
	/** The session's primary model. */
	model?: { provider: string; id: string };
	sessionManager: { getSessionId(): string };
	/** The session's working directory, against which a `bash` call's `cwd` resolves. */
	cwd: string;
};
/** One AgentMessage of that context, read by `role` (user, assistant, toolResult, custom, compactionSummary, …). */
export type Message = { role: string } & Record<string, unknown>;

/** The module's one object guard: transcript and overlay data are untyped JSON/YAML. */
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const byteLength = (text: string) => Buffer.byteLength(text, "utf8");
const messageOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

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
function isKilled(overlayText: string | undefined, slug: string, onUnparsable?: (error: unknown) => void): boolean {
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

// Where a `dispatch` command's own text ends: `dispatchCommandHead` and its helpers below are the
// rules @sjawhar/pi-envoy applies (`packages/pi-shared/src/shell-command.ts` in sjawhar/legion),
// copied because this extension cannot import the plugin; keep the two in step. A conservative
// character scan, not a shell parser: whatever it cannot reason about is no `dispatch` command.
// Whitespace is bash's blanks, space and tab, never JavaScript's `\s`, which also takes a no-break
// space that bash reads as part of a word.

/** The one here-document a command's first line may end with, opened with a quoted delimiter so
 * the shell expands nothing in its body. The second group is `-` for `<<-`, the only form under
 * which bash strips leading tabs from the delimiter line. */
const HEREDOC_OPENING = /^(.*?)[ \t]*<<(-?)[ \t]*'([A-Za-z_][A-Za-z0-9_]*)'[ \t]*$/;

/** A line of nothing but blanks, which bash runs as no command. */
const BLANK_LINE = /^[ \t]*$/;

/** Outside quotes, a `dispatch` head refuses whatever would end the command, start
 * another, expand something, redirect, start a comment (bash reads the rest of the line as one, a
 * here-document opener included) or escape a character (the shell's to interpret, so never
 * modelled here). */
const REFUSED_UNQUOTED = "\n;&|$`<>()#\\";

/** Inside double quotes bash still expands `$` and backticks and interprets `\`. */
const REFUSED_DOUBLE_QUOTED = "$`\\";

/** A `dispatch` command's first line, without its here-document opener, when the command is one
 * `dispatch` command, optionally fed one quoted here-document on stdin; undefined for any other
 * command. */
export function dispatchCommandHead(command: unknown): string | undefined {
	if (typeof command !== "string" || hasControlCharacter(command)) return undefined;
	// Blank lines before the head and after the command's last line run as no command, so they are
	// dropped whole. No other line is trimmed: bash ends a here-document only at a line that is
	// exactly its delimiter (with leading tabs stripped under `<<-`), whatever blanks the line
	// carries before or after it.
	const all = command.split("\n");
	const first = all.findIndex(line => !BLANK_LINE.test(line));
	if (first === -1) return undefined;
	const last = all.findLastIndex(line => !BLANK_LINE.test(line));
	const lines = all.slice(first, last + 1);
	let head = lines[0] ?? "";
	if (lines.length > 1) {
		const opening = HEREDOC_OPENING.exec(head);
		if (opening === null) return undefined;
		const stripTabs = opening[2] === "-";
		const delimiter = opening[3];
		const end = lines.findIndex((line, index) => index > 0 && (stripTabs ? line.replace(/^\t+/, "") : line) === delimiter);
		// The first line equal to the delimiter ends the here-document in the shell, so it must be
		// the last line: anything after it would run as a command.
		if (end !== lines.length - 1) return undefined;
		head = opening[1] ?? "";
	}
	head = head.replace(/^[ \t]+|[ \t]+$/g, "");
	return firstWord(head) === "dispatch" && headScan(head) ? head : undefined;
}

/** The trimmed head's first word as bash splits it, at a space or a tab. */
function firstWord(head: string): string {
	return head.split(/[ \t]/, 1)[0] ?? "";
}

/** Any control character but newline and tab: a `\r` before a line end would make the delimiter
 * this scan finds differ from the one bash finds (bash ends a here-document at `EOF\r`, not `EOF`). */
function hasControlCharacter(text: string): boolean {
	for (let index = 0; index < text.length; index += 1) {
		const code = text.charCodeAt(index);
		if ((code < 0x20 && code !== 0x0a && code !== 0x09) || code === 0x7f) return true;
	}
	return false;
}

/** Whether a `dispatch` head is one command whose words the shell takes as written:
 * single quotes make every character literal, and an unterminated quote is refused. */
function headScan(head: string): boolean {
	let quote: '"' | "'" | undefined;
	for (const char of head) {
		if (quote === "'") {
			if (char === "'") quote = undefined;
			continue;
		}
		if (quote === '"') {
			if (char === '"') quote = undefined;
			else if (REFUSED_DOUBLE_QUOTED.includes(char)) return false;
			continue;
		}
		if (char === "'" || char === '"') quote = char;
		else if (REFUSED_UNQUOTED.includes(char)) return false;
	}
	return quote === undefined;
}

type Word = { raw: string; value: string };

/** A head's words as bash splits them at unquoted blanks, each as written and with its quotes
 * removed. Only for a head `dispatchCommandHead` accepted, which holds no escape or expansion. */
function headWords(head: string): Word[] {
	const words: Word[] = [];
	let word: Word | undefined;
	let quote: string | undefined;
	for (const char of head) {
		if (quote === undefined && (char === " " || char === "\t")) {
			if (word) words.push(word);
			word = undefined;
			continue;
		}
		word ??= { raw: "", value: "" };
		word.raw += char;
		if (quote === undefined && (char === "'" || char === '"')) quote = char;
		else if (char === quote) quote = undefined;
		else word.value += char;
	}
	if (word) words.push(word);
	return words;
}

/** A scoped `dispatch` command: the tool it calls, the text to run (an override flag removed),
 * the same text with `--dry-run` after the command name, and the override's line. */
export interface ScopedCommand { tool: string; label: string; command: string; dryRunScript: string; rebuttal?: string }

/** The `bash` command as a scoped `dispatch` call, or undefined for any other command. */
export function scopedCommand(command: unknown): ScopedCommand | undefined {
	const head = dispatchCommandHead(command);
	if (head === undefined || typeof command !== "string") return undefined;
	const [program, name, ...flags] = headWords(head);
	if (name === undefined || !(SCOPED_COMMANDS as readonly string[]).includes(name.value)) return undefined;
	if (flags.some(flag => NO_SEND_FLAGS.includes(flag.value))) return undefined;
	if (name.value === SPEC_ONLY_COMMAND && !flags.some(flag => SPEC_FLAGS.includes(flag.value.split("=", 1)[0]))) return undefined;
	// An override is `--advisor-rebuttal <line>` or `--advisor-rebuttal=<line>`; a value that starts
	// with `--` is the next flag, as the CLI reads it. A blank line is no override and stays put.
	let rebuttal: string | undefined;
	const kept: Word[] = [];
	for (let index = 0; index < flags.length; index++) {
		const { value } = flags[index];
		const next = flags[index + 1]?.value;
		const line = value === REBUTTAL_FLAG && next !== undefined && !next.startsWith("--") ? next : value.startsWith(`${REBUTTAL_FLAG}=`) ? value.slice(REBUTTAL_FLAG.length + 1) : undefined;
		if (line === undefined || !line.trim()) {
			kept.push(flags[index]);
			continue;
		}
		rebuttal = line.trim();
		if (value === REBUTTAL_FLAG) index++;
	}
	// The head is the command's first non-blank text, so its first occurrence is where it stands.
	const at = command.indexOf(head);
	const withHead = (words: string[]) => `${command.slice(0, at)}${words.join(" ")}${command.slice(at + head.length)}`;
	const rest = kept.map(word => word.raw);
	return {
		tool: `dispatch_${name.value.replaceAll("-", "_")}`,
		label: `dispatch ${name.value}`,
		command: withHead([program.raw, name.raw, ...rest]),
		dryRunScript: withHead([program.raw, name.raw, "--dry-run", ...rest]),
		...(rebuttal === undefined ? {} : { rebuttal }),
	};
}

/** Runs `script` under bash in a process group of its own, which is killed at the limit or on the signal. */
export async function spawnDryRun(script: string, { cwd, env, timeoutMs, signal }: DryRunOptions): Promise<DryRunResult> {
	const child = Bun.spawn(["bash", "-c", script], { cwd, env, stdin: "ignore", stdout: "pipe", stderr: "pipe", detached: true });
	let timedOut = false;
	const kill = () => {
		try {
			process.kill(-child.pid, "SIGKILL");
		} catch {
			// The group already exited.
		}
	};
	const timer = setTimeout(() => {
		timedOut = true;
		kill();
	}, timeoutMs);
	signal?.addEventListener("abort", kill, { once: true });
	try {
		const [stdout, stderr] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text()]);
		await child.exited;
		return { exitCode: child.exitCode, stdout, stderr, timedOut };
	} finally {
		clearTimeout(timer);
		signal?.removeEventListener("abort", kill);
	}
}

function breakerKey(toolName: string, input: Input): string {
	let key = toolName;
	for (const k of BREAKER_KEYS) if (input[k] !== undefined) key += `\u0000${k}=${String(input[k])}`;
	return key;
}

/** Splits a top-level REBUTTAL_KEY off a device's JSON arguments; anything else comes back unchanged. */
function extractRebuttal(content: string): { rebuttal?: string; content: string } {
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

function renderArgs(input: Input): string {
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
/** Untrusted text escaped, then cut to `max` bytes: no transcript text can close or open a tag. */
const escaped = (text: string, max = Number.POSITIVE_INFINITY) => clipBytes(escapeXml(text), max);

function renderAssistant(content: unknown): string {
	const parts: string[] = [];
	for (const block of Array.isArray(content) ? content : []) {
		if (!isRecord(block)) continue;
		if (block.type === "text" && typeof block.text === "string" && block.text.trim()) parts.push(escaped(block.text));
		else if (block.type === "thinking" && typeof block.thinking === "string" && block.thinking.trim()) parts.push(`(thinking) ${escaped(block.thinking, 1024)}`);
		else if (block.type === "toolCall") {
			const args = typeof block.arguments === "string" ? block.arguments : JSON.stringify(block.arguments ?? {});
			parts.push(`→ ${escaped(String(block.name))}(${escaped(args, 2048)})`);
		}
	}
	return parts.length > 0 ? `**agent**: ${parts.join("\n")}` : "";
}

function renderMessage(message: Message): string {
	switch (message.role) {
		case "user":
			return `**user**: ${escaped(textOf(message.content))}`;
		case "assistant":
			return renderAssistant(message.content);
		case "toolResult":
			return `⇒ ${escaped(String(message.toolName))}${message.isError ? " (error)" : ""}: ${escaped(textOf(message.content), 8 * 1024)}`;
		case "bashExecution":
			return message.excludeFromContext ? "" : `→ user-bash ${escaped(String(message.command))} ⇒ ${escaped(String(message.output ?? ""), 8 * 1024)}`;
		case "pythonExecution":
			return message.excludeFromContext ? "" : `→ user-python ${escaped(String(message.code))} ⇒ ${escaped(String(message.output ?? ""), 8 * 1024)}`;
		case "developer":
			return `**system**: ${escaped(textOf(message.content), 2 * 1024)}`;
		case "custom":
		case "hookMessage": {
			const text = textOf(message.content);
			// A Dispatch answer from a human arrives this way: in full, even when hidden.
			if (message.customType === "envoy-message") return `<primary-message kind="envoy-message">\n${escaped(text)}\n</primary-message>`;
			if (message.display === false && message.customType !== "advisor") return "";
			return `[${escaped(String(message.customType))}] ${escaped(text.replace(/\s+/g, " ").trim().slice(0, 120))}`;
		}
		case "compactionSummary":
			return `[compaction] ${escaped(String(message.summary ?? ""), 8 * 1024)}`;
		case "branchSummary":
			return `[branch] ${escaped(String(message.summary ?? ""), 2 * 1024)}`;
		default:
			return "";
	}
}

/** The newest whole messages of the primary's context that fit in `capBytes`, oldest first, behind an elision line when older ones were dropped. */
export function renderTranscript(messages: readonly Message[], capBytes: number): string {
	const rendered = messages.map(renderMessage).filter(text => text !== "");
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
		// The newest message alone overflows the window: keep its head.
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
class Breaker {
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
	clear(): void {
		this.#reasons.clear();
	}
	reasons(key: string): readonly string[] {
		return this.#reasons.get(key) ?? [];
	}
}

/** `{{name}}` placeholders replaced in one pass, values pasted verbatim (never re-scanned, never read as `$` patterns); a placeholder with no value throws. */
export function renderTemplate(template: string, values: Record<string, string>): string {
	return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => {
		if (!Object.hasOwn(values, name)) throw new Error(`AskGate prompt template names {{${name}}}, which has no value`);
		return values[name];
	});
}

/** The gate's user message: the agent's system prompt and transcript as escaped data, then the call. */
function renderRequest(primarySystemPrompt: string, transcript: string, tool: string, input: Input, priorReasons: readonly string[]): string {
	const prior =
		priorReasons.length > 0
			? // Each reason is model text inside the governing request: escaped, and every whitespace run
				// (any line break included: CR, VT, FF, U+0085, U+2028, U+2029) folded to one space, so none
				// can start a line of its own, such as a forged `### Gate request`.
				`You answered revise ${priorReasons.length} time(s) for this target since its last allowed call:\n${priorReasons.map(r => `- ${escapeXml(r.replace(/[\s\u0085]+/g, " "))}`).join("\n")}`
			: "";
	return renderTemplate(requestTemplate, { primarySystemPrompt: escapeXml(primarySystemPrompt), transcript, tool, args: renderArgs(input), priorReasons: prior });
}

/**
 * The one transform for text handed to the agent (a model's reason, the agent's own rebuttal): verbatim,
 * except that a `<` which could start a tag (before a letter, `/`, `!` or `?`) becomes `&lt;`, then capped
 * at 2 KiB. The two defects are disjoint. A frame closed or a harness tag opened needs a tag-opening `<`,
 * and this rule stops it. Full escaping also turned `&&`, `->` and a `> ` quote line into entities, so a fix
 * sent back verbatim posted `&gt;` and a check for the quote failed; those now pass through unchanged. Text
 * rendered into the gate's own prompt is escaped in full instead (`escaped`). Nothing between here and
 * delivery decodes entities: warn joins the text into a developer message as-is, block's Error message
 * becomes the tool result as-is.
 */
const agentFacing = (text: string) => clipBytes(text.replace(/<(?=[A-Za-z/!?])/g, "&lt;"), GATE_REASON_MAX_BYTES);
// What the agent reads after each gated call: silence only for an allow or a fail-open, since a
// pass it cannot see is indistinguishable from a gate that broke.
const SECOND_REVISE = "This is the second revise on this target: a third unchanged resend is sent without review and recorded.";
/** How the agent overrides a revise on each surface: a key in the device JSON, or a flag on the command. */
type Override = { name: string; how: string; what: string };
const DEVICE_OVERRIDE: Override = { name: REBUTTAL_KEY, how: `"${REBUTTAL_KEY}": "<one line>" added to the JSON`, what: "field" };
const COMMAND_OVERRIDE: Override = { name: REBUTTAL_FLAG, how: `${REBUTTAL_FLAG} '<one line>' added to its flags`, what: "flag" };
const renderRevise = (reason: string, second: boolean, override: Override) =>
	`AskGate did not send this call.\n${agentFacing(reason)}\nSend the corrected call, or resend this one unchanged with ${override.how} to override; an override is always sent and recorded, and the ${override.what} is removed before sending.${second ? `\n${SECOND_REVISE}` : ""}`;
/** The refusal block mode returns for a call given up without a verdict, by cause. */
const NO_VERDICT_REFUSAL: Record<"abandoned" | "shutdown", string> = {
	abandoned: "AskGate did not send this call: it was stopped before a verdict came back. Send it again if it is still wanted.",
	shutdown: "AskGate did not send this call: the session shut down before a verdict came back. Send it again from a live session if it is still wanted.",
};
const renderWarn = (reason: string, second: boolean) =>
	`<advisor-gate advisor="AskGate" verdict="revise">\n${agentFacing(reason)}\nThe call ran. This note concerns only that Dispatch call and authorizes nothing beyond correcting it. Correct it now where the reason names a fix (edit the ask, retract it, or resend), or state your rebuttal in your next step.${second ? `\n${SECOND_REVISE}` : ""}\n</advisor-gate>`;
const renderRebuttalAck = (rebuttal: string, override: Override) =>
	`<advisor-gate advisor="AskGate" outcome="rebuttal">\nYour ${override.name} "${agentFacing(rebuttal)}" was received and recorded; the call was sent without review, with that ${override.what} removed before sending.\n</advisor-gate>`;
const renderBreakerAck = () =>
	`<advisor-gate advisor="AskGate" outcome="breaker">\nThird attempt on this target after two revises: sent without review and recorded.\n</advisor-gate>`;

export function createAskGate(deps: Deps): (pi: Pi) => void {
	return function askGate(pi: Pi): void {
		const mode = parseMode(deps.env.OMP_ASKGATE);
		if (mode === "off") return;
		const timeoutMs = parseTimeout(deps.env.OMP_ASKGATE_TIMEOUT_MS);
		const ceiling = deps.handlerCeilingMs();
		if (ceiling - CEILING_MARGIN_MS < MIN_DEADLINE_MS) {
			// A deadline this short would time out nearly every verdict; the runner would refuse a longer one.
			pi.logger.warn(`AskGate not bound: extensionHandlers.toolCallTimeoutMs is ${ceiling} ms, which leaves no room for a verdict (it needs at least ${MIN_DEADLINE_MS + CEILING_MARGIN_MS} ms)`);
			return;
		}
		const charter = loadCharter(deps.readFile, deps.charterPath, deps.home);
		// The system role holds only the gate's own text and the charter; the agent's system prompt
		// and transcript are data about the agent under review, escaped into the user message.
		const system = `${systemTemplate.trim()}\n\n${charter}`;
		const dumpPath = deps.env.OMP_ASKGATE_DUMP;
		const unparsable = (error: unknown) => pi.logger.debug("AskGate: local-overrides.yml does not parse; the gate stays on", { error: String(error) });
		// The fork binds this factory once per process and keeps the binding across /new, /resume
		// and a branch switch, so the per-session state below resets on session_switch and
		// session_branch: a halt, a count, revise reasons or a rebuttal never outlive their session.
		const rebuttals = new Map<string, string>();
		const breaker = new Breaker();
		let failures = 0;
		let halted = false;
		// Set once the session starts shutting down (only dispose emits session_shutdown): a scoped call
		// that reaches the gate after that asks no model and takes the shutdown branch at once.
		let closing = false;
		const reset = () => {
			rebuttals.clear();
			breaker.clear();
			failures = 0;
			halted = false;
		};
		pi.on("session_switch", reset);
		pi.on("session_branch", reset);
		// A gate still waiting when its tool's execution ends was abandoned: the runner gave up on the
		// handler (a user abort, or its ceiling) and the call already went its way. Cancel the model call.
		// An eval-bridged write never shows its own id on a loop tool_execution_end, so a run the user
		// stopped (its last assistant message aborted) abandons every gate still waiting. Any other end
		// leaves them be: the session may continue (willContinue), or a backgrounded eval cell may keep
		// running past the run, and abandoning its gate would let its write out unchecked. A call a cell
		// makes after it finished, from its own timer or thread, never reaches a gate: both eval bridges
		// refuse a call from a finished cell ("Run no longer active", "No active Python tool bridge
		// session").
		/** Why a waiting gate was given up: the user or the runner ended its call or run, or the session shut down. */
		type Abandon = (cause: "abandoned" | "shutdown", graceMs?: number) => void;
		const inflight = new Map<string, Abandon>();
		pi.on("tool_execution_end", (event: { toolCallId: string }) => {
			inflight.get(event.toolCallId)?.("abandoned");
		});
		pi.on("agent_end", (event: { messages?: readonly Message[]; willContinue?: boolean }) => {
			if (event.willContinue) return;
			if (event.messages?.findLast(m => m.role === "assistant")?.stopReason !== "aborted") return;
			for (const abandon of inflight.values()) abandon("abandoned");
		});
		// A gate still waiting at shutdown (left running past a normal end) would bill to its deadline and
		// record into a sealed session. Abandon it, and hold shutdown until its entry is written; a gated
		// call that starts after this point asks no model (`closing`). Either way the entry reads
		// `shutdown`, and block mode refuses the call as it refuses any call given up without a verdict.
		const handling = new Set<Promise<ToolCallResult>>();
		pi.on("session_shutdown", async () => {
			closing = true;
			for (const abandon of inflight.values()) abandon("shutdown", SHUTDOWN_GRACE_MS);
			const { promise: waited, resolve: stopWaiting } = Promise.withResolvers<void>();
			const timer = setTimeout(stopWaiting, SHUTDOWN_WAIT_MS);
			await Promise.race([Promise.allSettled([...handling]), waited]);
			clearTimeout(timer);
		});

		/** A call without a verdict counts toward the halt; a verdict resets the count; anything else leaves it. */
		const settle = (effect: Decision["effect"], ctx: GateCtx) => {
			if (effect === "verdict") failures = 0;
			if (effect !== "failure") return;
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
		/** What one gated call records, returns, and does to the halt count. */
		type Decision = { fields: Pick<GateEntry, "decision" | "outcome"> & Partial<GateEntry>; result?: ToolCallResult; effect: "failure" | "verdict" | "none" };
		const pass = (outcome: GateEntry["outcome"], fields: Partial<GateEntry> = {}, effect: Decision["effect"] = "none"): Decision => ({ fields: { decision: "allow", outcome, ...fields }, effect });
		/**
		 * A call given up without a verdict: its call, its run or the session ended first. Not the gate's
		 * failure, so it never counts toward the halt. No verdict is not permission: block mode refuses it
		 * (defence in depth: the runner awaiting it has usually refused the call already), warn mode lets it
		 * go. The cause stays recorded.
		 */
		const givenUp = (cause: "abandoned" | "shutdown", fields: Partial<GateEntry> = {}): Decision =>
			mode === "block"
				? { fields: { decision: "revise", outcome: cause, ...fields }, result: { block: true, reason: NO_VERDICT_REFUSAL[cause] }, effect: "none" }
				: pass(cause, fields);

		const decide = async (event: ToolCallEvent, ctx: GateCtx, started: number, base: Base, key: string, rebuttal: string | undefined, override: Override): Promise<Decision> => {
			if (isKilled(deps.readFile(overlayPath(deps.env, deps.home)), SLUG, unparsable)) return pass("killed");
			if (rebuttal !== undefined) {
				breaker.reset(key);
				return { fields: { decision: "allow", outcome: "rebuttal", rebuttal }, result: { additionalContext: renderRebuttalAck(rebuttal, override) }, effect: "none" };
			}
			if (halted) return pass("halted");
			if (breaker.tripped(key)) {
				breaker.reset(key);
				return { fields: { decision: "allow", outcome: "breaker" }, result: { additionalContext: renderBreakerAck() }, effect: "none" };
			}

			if (closing) return givenUp("shutdown");
			const model = ctx.models.resolve(MODEL_ROLE);
			if (!model) return pass("unavailable", {}, "failure");
			// A session routed off the gate's provider keeps its transcript there: nothing is rendered or sent.
			if (ctx.model?.provider !== model.provider) return pass("skipped", { reason: `primary on ${ctx.model?.provider ?? "no model"}` });

			const transcript = renderTranscript(deps.contextMessages(ctx), GATE_CONTEXT_MAX_BYTES);
			const user = renderRequest(ctx.getSystemPrompt().join("\n\n"), transcript, base.path, event.input, breaker.reasons(key));
			const promptBytes = byteLength(system) + byteLength(user);
			if (dumpPath) deps.appendFile(dumpPath, `===== AskGate ${event.toolCallId} system =====\n${system}\n===== AskGate ${event.toolCallId} user =====\n${user}\n\n`);

			// Raced from handler entry, so the render above counts against the runner's ceiling too.
			const deadlineMs = Math.min(timeoutMs, deps.handlerCeilingMs() - CEILING_MARGIN_MS);
			const request = { ctx, model, system, user, sessionId: ctx.sessionManager.getSessionId() };
			const answer = await ask(request, event.toolCallId, Math.max(0, deadlineMs - (deps.now() - started)));
			const called = { promptBytes, model: `${model.provider}/${model.id}`, deadlineMs, ...(answer.usage ? { usage: answer.usage } : {}) };
			if (answer.kind === "timeout") return pass("timeout", called, "failure");
			if (answer.kind === "abandoned" || answer.kind === "shutdown") return givenUp(answer.kind, called);
			if (answer.kind === "error") return pass("error", { reason: answer.reason, ...called }, "failure");
			const verdict = parseVerdict(answer.text);
			if (!verdict) return pass("no-verdict", { raw: answer.text.slice(-2000), ...called }, "failure");
			if (verdict.decision === "allow" || verdict.reason === undefined) {
				breaker.reset(key);
				return pass("verdict", called, "verdict");
			}
			breaker.revised(key, verdict.reason);
			const second = breaker.reasons(key).length === GATE_BREAKER_REVISES;
			return {
				fields: { decision: "revise", outcome: "verdict", reason: verdict.reason, deliveredReason: agentFacing(verdict.reason), revisesForKey: base.revisesForKey + 1, ...called },
				result: mode === "block" ? { block: true, reason: renderRevise(verdict.reason, second, override) } : { additionalContext: renderWarn(verdict.reason, second) },
				effect: "verdict",
			};
		};

		/** The model call raced against the deadline and against its tool ending without it. */
		type Answer = { kind: "answer"; text: string; usage?: Usage } | { kind: "error"; reason: string; usage?: Usage } | { kind: "timeout" | "abandoned" | "shutdown"; usage?: Usage };
		const ask = async (req: Omit<CompleteRequest, "signal">, toolCallId: string, remainingMs: number): Promise<Answer> => {
			const controller = new AbortController();
			const { promise: stopped, resolve: stop } = Promise.withResolvers<"timeout" | "abandoned" | "shutdown">();
			const timer = setTimeout(() => stop("timeout"), remainingMs);
			let graceMs = ABORT_GRACE_MS;
			inflight.set(toolCallId, (cause, grace = ABORT_GRACE_MS) => {
				graceMs = grace;
				stop(cause);
			});
			try {
				const call = deps.complete({ ...req, signal: controller.signal });
				// Promise.race subscribes to the completion, so a late rejection is handled.
				const first = await Promise.race([call, stopped]);
				if (typeof first !== "string") {
					return first.error !== undefined ? { kind: "error", reason: first.error, usage: first.usage } : { kind: "answer", text: first.text, usage: first.usage };
				}
				controller.abort();
				// The aborted request was billed for what it sent; give it a moment to report that.
				const { promise: grace, resolve: graceOver } = Promise.withResolvers<undefined>();
				const graceTimer = setTimeout(graceOver, graceMs);
				const settled = await Promise.race([call.catch(() => undefined), grace]);
				clearTimeout(graceTimer);
				return { kind: first, usage: settled?.usage };
			} catch (error) {
				return { kind: "error", reason: messageOf(error) };
			} finally {
				clearTimeout(timer);
				inflight.delete(toolCallId);
			}
		};

		/** A scoped command's arguments as the CLI's own --dry-run prints them, or why there are none. */
		type DryRun = { args: Input } | { failure: string } | { cause: "abandoned" | "shutdown" };
		const readArgs = async (scoped: ScopedCommand, input: Input, ctx: GateCtx, toolCallId: string): Promise<DryRun> => {
			// The bash tool's own resolution: `~` is home, a bare `/` is the session's directory, and a
			// relative path resolves against it.
			const raw = typeof input.cwd === "string" && input.cwd !== "" ? input.cwd.replace(/^~(?=$|\/)/, deps.home) : undefined;
			const cwd = raw === undefined || /^\/+$/.test(raw) ? ctx.cwd : path.resolve(ctx.cwd, raw);
			// Abandoned like a waiting model call: the run's stop or the session's shutdown kills the dry-run.
			const controller = new AbortController();
			let cause: "abandoned" | "shutdown" | undefined;
			inflight.set(toolCallId, given => {
				cause = given;
				controller.abort();
			});
			let run: DryRunResult;
			try {
				run = await deps.dryRun(scoped.dryRunScript, { cwd, env: deps.env, timeoutMs: DRY_RUN_TIMEOUT_MS, signal: controller.signal });
			} finally {
				inflight.delete(toolCallId);
			}
			if (cause !== undefined) return { cause };
			if (run.timedOut) return { failure: `${scoped.label} --dry-run did not finish within ${DRY_RUN_TIMEOUT_MS} ms` };
			const printed = clipBytes((run.stdout || run.stderr).trim(), GATE_REASON_MAX_BYTES);
			if (run.exitCode !== 0) return { failure: `${scoped.label} --dry-run exited ${run.exitCode ?? "on a signal"}: ${printed}` };
			try {
				const args: unknown = JSON.parse(run.stdout);
				if (isRecord(args)) return { args };
			} catch {
				// Not JSON: reported below.
			}
			return { failure: `${scoped.label} --dry-run printed no JSON object: ${printed}` };
		};

		/** The fields every entry of a call carries; a gated call fills in its target's revise count and the digest. */
		const identity = (event: ToolCallEvent): Base => ({
			advisor: "AskGate",
			tool: event.toolName,
			path: `xd://${event.toolName}`,
			toolCallId: event.toolCallId,
			verdictMode: mode,
			revisesForKey: 0,
			argsDigest: "",
		});

		const handleCall = async (event: ToolCallEvent, ctx: GateCtx): Promise<ToolCallResult> => {
			const started = deps.now();
			let base: Base | undefined;
			let decision: Decision;
			// A `dispatch` command with its override flag removed, which runs whatever the gate decides.
			let revised: Input | undefined;
			try {
				if (ctx.agent.kind !== "main") return undefined;
				if (event.toolName === "write") return stripRebuttal(event);
				if (event.toolName === "bash") {
					const scoped = scopedCommand(event.input.command);
					if (scoped === undefined) return undefined;
					if (scoped.rebuttal !== undefined) revised = { ...event.input, command: scoped.command };
					base = { ...identity(event), tool: scoped.tool, path: scoped.label };
					const run = await readArgs(scoped, event.input, ctx, event.toolCallId);
					if ("cause" in run) decision = givenUp(run.cause);
					// The CLI refuses arguments it cannot parse before it sends anything, so the command runs.
					else if ("failure" in run) decision = pass("error", { reason: run.failure });
					else {
						const key = breakerKey(scoped.tool, run.args);
						base = { ...base, revisesForKey: breaker.reasons(key).length, argsDigest: createHash("sha256").update(JSON.stringify(run.args)).digest("hex") };
						decision = await decide({ toolName: scoped.tool, toolCallId: event.toolCallId, input: run.args }, ctx, started, base, key, scoped.rebuttal, COMMAND_OVERRIDE);
					}
				} else {
					// A remembered rebuttal is spent by its device event, whatever happens to the call.
					const rebuttal = rebuttals.get(event.toolCallId);
					rebuttals.delete(event.toolCallId);
					if (!scopedDevice(event.toolName, event.input)) return undefined;
					const key = breakerKey(event.toolName, event.input);
					base = {
						...identity(event),
						revisesForKey: breaker.reasons(key).length,
						argsDigest: createHash("sha256").update(JSON.stringify(event.input)).digest("hex"),
					};
					decision = await decide(event, ctx, started, base, key, rebuttal, DEVICE_OVERRIDE);
				}
			} catch (error) {
				// Fail open: the runner turns a thrown handler into a refusal. A throw here is the
				// gate's own (the overlay, the dump, the render, the dry-run's spawn), not a missing
				// verdict, so it does not count toward the halt.
				decision = pass("error", { reason: messageOf(error) });
			}
			// Settled once and recorded once, each on its own, so neither failing can repeat the other.
			try {
				settle(decision.effect, ctx);
			} catch {
				// The halt notice is best effort.
			}
			try {
				pi.appendEntry(ADVISOR_GATE_ENTRY_TYPE, { ...(base ?? identity(event)), ...decision.fields, latencyMs: deps.now() - started } satisfies GateEntry);
			} catch {
				// The entry is best effort; the call still runs.
			}
			return revised === undefined ? decision.result : { ...decision.result, input: revised };
		};

		pi.on("tool_call", async (event: ToolCallEvent, ctx: GateCtx): Promise<ToolCallResult> => {
			const done = handleCall(event, ctx);
			handling.add(done);
			try {
				return await done;
			} finally {
				handling.delete(done);
			}
		});
	};
}
