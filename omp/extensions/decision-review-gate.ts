// Refuse a method decision the Verified Linux stream lead sends to the stream's slices without a
// fresh reviewer's PASS verdict.
//
// The lead (omp session LEAD_SESSION_ID) owes a fresh reviewer on every method decision, and once
// sent one by Envoy without it. A program cannot judge a decision, but it can check that a method
// decision sent to the slices carries a reviewer's verdict; this is that check. The commit that
// added it records the incident.
//
// It acts on a call that sends an Envoy message: a `write` to `xd://envoy_send` or
// `xd://envoy_publish`, or a direct `envoy_send`/`envoy_publish` call (the tool unmounted, or the
// nested dispatch omp emits for a device write; that one is skipped when the write it came from was
// already let through, by tool call id). Only in the lead's session family: the session itself, or a
// subagent whose transcript sits under the lead's (rootSessionId, the experiments rule), since a
// subagent has the Envoy tools too. Every other session's calls are not looked at and not logged.
//
// A message is a method decision when it goes to a slice (METHOD_TOPICS, METHOD_SESSIONS, or the
// `notifications.agent.<id>` inbox topic of a METHOD_SESSIONS id) and mentions any of METHOD_WORDS,
// case-insensitive. It goes out only when it carries `review: <path>`, where <path> is an absolute
// path that resolves (through `..` and symlinks) to a regular file under the review directory,
// modified within the last 3 hours, whose last line starting `VERDICT:` starts `VERDICT: PASS` (a
// later FAIL outranks an earlier PASS). Otherwise the call is refused with a reason naming what is
// missing and how to get it. `no-method-decision: <reason>` lets a message through as an escape,
// for operational messages that only mention a method word.
//
// The review directory is $TMPDIR/verified-linux/reviews, read once at load; the refusal names it
// as this process resolved it.
//
// Each decision on an Envoy call in the lead's family (pass, refuse, escape, and the fail-open
// outcomes) is one JSON line in ~/.omp/decision-review-gate.jsonl, written after the decision and
// not awaited, so a failed log write never changes it. It fails open (one debug line in omp's log,
// one `error` or `timeout` line in the gate's log, and the call runs) on any exception, including
// device content that is not JSON, and when the check takes longer than 2 s
// (OMP_DECISION_REVIEW_GATE_MS overrides it): omp refuses a call whose tool_call handler outlives
// `extensionHandlers.toolCallTimeoutMs`, and a file read can hang. The running-script-guard.ts
// pattern.
import * as fsp from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { rootSessionId } from "./experiments/gates";

/** The Verified Linux stream lead's omp session; the gate acts in it and its subagents only. */
export const LEAD_SESSION_ID = "01a15f9c-6bee-7127-b4f1-907ec8d3176d";
/** Envoy topics that reach the stream's slices. */
export const METHOD_TOPICS = [
	"notifications.role.lead-verified-linux",
	"notifications.role.linux-direct-line",
	"notifications.role.linux-baseline",
	"notifications.team.linux-owners",
] as const;
/** Slice sessions an `envoy_send` reaches directly (and their `notifications.agent.<id>` inbox topics). */
export const METHOD_SESSIONS = ["01a1798a-9911-7231-a1bb-cf29d7985273", "01a1798a-995f-72e1-9cdd-783a522f597b", "01a1798a-99af-7266-95dd-fa4be35f9109"] as const;
/** Words that make a message to a slice a method decision, matched case-insensitive. */
export const METHOD_WORDS = [
	"INFRA-109",
	"owner method",
	"owners write",
	"owner skill",
	"planner",
	"implementer",
	"architect",
	"body proof",
	"skeleton",
	"two phases",
	"child issue",
	"method change",
] as const;

const REVIEW_MAX_AGE_MS = 3 * 60 * 60 * 1000;
const DEADLINE_MS = Number(process.env.OMP_DECISION_REVIEW_GATE_MS) > 0 ? Number(process.env.OMP_DECISION_REVIEW_GATE_MS) : 2_000;
const LOG_FILE = path.join(os.homedir(), ".omp", "decision-review-gate.jsonl");
const MESSAGE_EXCERPT = 500;
/** Write ids let through, whose nested device dispatch is not checked again; bounded. */
const MAX_REMEMBERED = 256;
const REVIEW_LINE = /\breview:[ \t]*(\S+)/gi;
const ESCAPE_LINE = /no-method-decision:[ \t]*([^\n]*)/gi;

type EnvoyTool = "envoy_send" | "envoy_publish";
type Call = { tool: EnvoyTool; via: "write" | "direct" };
type CallEvent = { toolName: string; toolCallId: string; input: Record<string, unknown> };
type Ctx = { sessionManager: { getSessionId(): string; getSessionFile(): string | undefined } };
type ReviewFs = {
	realpath(p: string): Promise<string>;
	stat(p: string): Promise<{ isFile(): boolean; mtimeMs: number }>;
	readFile(p: string): Promise<string>;
};
export type GateDeps = {
	reviewDir: string;
	/** Writes one log line; the gate does not wait for it. */
	append: (line: string) => void | Promise<void>;
	now: () => number;
	deadlineMs: number;
	fs: ReviewFs;
};
/** One decision: the log entry's own fields, and the refusal when there is one. */
type Evaluation = { entry: Record<string, unknown>; refusal?: string };

const isEnvoyTool = (name: string): name is EnvoyTool => name === "envoy_send" || name === "envoy_publish";

/** The Envoy call this tool call makes, if any; reads no content, so it never throws. */
function classify(event: CallEvent): Call | undefined {
	if (isEnvoyTool(event.toolName)) return { tool: event.toolName, via: "direct" };
	if (event.toolName !== "write" || typeof event.input?.path !== "string") return undefined;
	const url = event.input.path.trim().toLowerCase();
	if (!url.startsWith("xd://")) return undefined;
	const device = url.slice("xd://".length);
	return isEnvoyTool(device) ? { tool: device, via: "write" } : undefined;
}

/** The tool's arguments; a device write's JSON content throws when it does not parse. */
function argsOf(call: Call, input: Record<string, unknown>): Record<string, unknown> {
	if (call.via === "direct") return input;
	const parsed: unknown = typeof input.content === "string" ? JSON.parse(input.content) : input.content;
	return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {};
}

function isMethodTarget(tool: EnvoyTool, target: string): boolean {
	const sessions: readonly string[] = METHOD_SESSIONS;
	if (tool === "envoy_send") return sessions.includes(target);
	return (METHOD_TOPICS as readonly string[]).includes(target) || sessions.some(id => target === `notifications.agent.${id}`);
}

function methodWordsIn(message: string): string[] {
	const lower = message.toLowerCase();
	return METHOD_WORDS.filter(word => lower.includes(word.toLowerCase()));
}

/** The first non-empty `no-method-decision:` reason. */
function escapeReason(message: string): string | undefined {
	for (const match of message.matchAll(ESCAPE_LINE)) {
		const reason = match[1].trim();
		if (reason) return reason;
	}
	return undefined;
}

/** Every `review:` path, without the quotes, backticks or trailing punctuation prose wraps it in. */
function reviewPaths(message: string): string[] {
	return [...message.matchAll(REVIEW_LINE)].map(match => match[1].replace(/^[`"'<([]+/, "").replace(/[`"'>)\],.;:]+$/, "")).filter(Boolean);
}

function age(ms: number): string {
	const minutes = Math.floor(ms / 60_000);
	return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}

/** Why this review path does not clear a method decision, or undefined when it does. */
async function reviewProblem(raw: string, now: number, deps: GateDeps): Promise<string | undefined> {
	const outside = `review: ${raw} is not a file under ${deps.reviewDir}/`;
	if (!path.isAbsolute(raw)) return outside;
	let real: string;
	try {
		real = await deps.fs.realpath(raw);
	} catch (error) {
		const code = (error as NodeJS.ErrnoException).code;
		return code === "ENOENT" || code === "ENOTDIR" ? `review: ${raw} does not exist` : `review: ${raw} cannot be read (${code ?? String(error)})`;
	}
	let realDir: string;
	try {
		realDir = await deps.fs.realpath(deps.reviewDir);
	} catch {
		return outside;
	}
	if (!real.startsWith(realDir + path.sep)) return outside;
	const stat = await deps.fs.stat(real);
	if (!stat.isFile()) return `review: ${raw} is not a regular file`;
	const old = now - stat.mtimeMs;
	if (old > REVIEW_MAX_AGE_MS) return `review: ${raw} was last modified ${age(old)} ago, and a review counts for 3 hours after it is written`;
	const verdicts = (await deps.fs.readFile(real)).split(/\r?\n/).filter(line => line.startsWith("VERDICT:"));
	const last = verdicts.at(-1);
	if (last === undefined) return `review: ${raw} has no line starting VERDICT: PASS`;
	if (!/^VERDICT: PASS\b/.test(last)) return `review: ${raw} does not end on VERDICT: PASS (its last verdict line is ${JSON.stringify(last.trim().slice(0, 120))})`;
	return undefined;
}

function refusal(tool: EnvoyTool, target: string, words: string[], problems: string[], reviewDir: string): string {
	return [
		`Decision-review gate: nothing was sent. This ${tool} to ${target} reads as a method decision (it mentions ${words.map(w => JSON.stringify(w)).join(", ")}), and ${problems.join("; ")}.`,
		`A method decision goes to the Verified Linux slices only with a fresh reviewer's PASS: dispatch a fresh reviewer (task agent reviewer or scout) with the decision text, INFRA-109 (dispatch doc-read --issue INFRA-109) and dispatch://LINUX-31/artifact/sami-decisions-md; have it write its verdict to ${reviewDir}/<name>.md ending with VERDICT: PASS or VERDICT: FAIL; then add review: <path> to the message.`,
		"If the message is not a method decision, add `no-method-decision: <reason>` to it instead.",
	].join("\n");
}

async function evaluate(call: Call, input: Record<string, unknown>, deps: GateDeps): Promise<Evaluation> {
	const now = deps.now();
	const args = argsOf(call, input);
	const rawTarget = call.tool === "envoy_send" ? args.session_id : args.topic;
	const target = typeof rawTarget === "string" ? rawTarget.trim() : "";
	const message = typeof args.message === "string" ? args.message : "";
	const methodWords = methodWordsIn(message);
	const common = { at: new Date(now).toISOString(), target, methodWords, message: message.slice(0, MESSAGE_EXCERPT) };
	if (!isMethodTarget(call.tool, target)) return { entry: { ...common, outcome: "pass", why: "not a method-decision target" } };
	if (methodWords.length === 0) return { entry: { ...common, outcome: "pass", why: "no method words" } };
	const escape = escapeReason(message);
	if (escape) return { entry: { ...common, outcome: "escape", escape } };
	const review = reviewPaths(message);
	const problems: string[] = [];
	for (const raw of review) {
		const problem = await reviewProblem(raw, now, deps);
		if (!problem) return { entry: { ...common, outcome: "pass", why: "review PASS", review } };
		problems.push(problem);
	}
	if (review.length === 0) problems.push("it carries no `review: <path>` line");
	return { entry: { ...common, outcome: "refuse", review, problems }, refusal: refusal(call.tool, target, methodWords, problems, deps.reviewDir) };
}

/** The lead's session or a subagent of it: its ids, for the log; undefined for any other session. */
function leadScope(ctx: Ctx): { session: string; rootSession: string } | undefined {
	const session = ctx.sessionManager.getSessionId();
	const rootSession = rootSessionId(ctx.sessionManager.getSessionFile(), session);
	return session === LEAD_SESSION_ID || rootSession === LEAD_SESSION_ID ? { session, rootSession } : undefined;
}

async function withDeadline<T>(work: Promise<T>, ms: number): Promise<T | "expired"> {
	const { promise: expired, resolve: expire } = Promise.withResolvers<"expired">();
	const timer = setTimeout(() => expire("expired"), ms);
	try {
		return await Promise.race([work, expired]);
	} finally {
		clearTimeout(timer);
	}
}

/** Appends to the log, which holds message text: owner-only, like the session files. */
async function appendLog(line: string): Promise<void> {
	await fsp.mkdir(path.dirname(LOG_FILE), { recursive: true, mode: 0o700 });
	await fsp.appendFile(LOG_FILE, line, { mode: 0o600 });
}

const DEFAULT_DEPS: GateDeps = {
	reviewDir: path.join(os.tmpdir(), "verified-linux", "reviews"),
	append: appendLog,
	now: Date.now,
	deadlineMs: DEADLINE_MS,
	fs: { realpath: p => fsp.realpath(p), stat: p => fsp.stat(p), readFile: p => fsp.readFile(p, "utf8") },
};

export function createDecisionReviewGate(overrides: Partial<GateDeps> = {}): (pi: ExtensionAPI) => void {
	const deps: GateDeps = { ...DEFAULT_DEPS, ...overrides };
	return pi => {
		const allowedWrites = new Set<string>();
		const allow = (call: Call, toolCallId: string) => {
			if (call.via !== "write") return;
			allowedWrites.add(toolCallId);
			if (allowedWrites.size > MAX_REMEMBERED) allowedWrites.delete(allowedWrites.values().next().value as string);
		};
		const record = (entry: Record<string, unknown>) => {
			const fail = (error: unknown) => pi.logger.debug("decision-review-gate: log write failed", { error: error instanceof Error ? error.message : String(error) });
			try {
				void Promise.resolve(deps.append(`${JSON.stringify(entry)}\n`)).catch(fail);
			} catch (error) {
				fail(error);
			}
		};

		pi.on("tool_call", async (event: CallEvent, ctx: Ctx) => {
			const call = classify(event);
			if (!call) return undefined;
			let base: Record<string, unknown> | undefined;
			try {
				const scope = leadScope(ctx);
				if (!scope) return undefined;
				if (call.via === "direct" && allowedWrites.delete(event.toolCallId)) return undefined;
				base = { ...scope, toolCallId: event.toolCallId, tool: call.tool, via: call.via };
				const result = await withDeadline(evaluate(call, event.input, deps), deps.deadlineMs);
				if (result === "expired") {
					pi.logger.debug("decision-review-gate: check took longer than its deadline; let through", { tool: call.tool, deadlineMs: deps.deadlineMs });
					record({ at: new Date().toISOString(), outcome: "timeout", deadlineMs: deps.deadlineMs, ...base });
					allow(call, event.toolCallId);
					return undefined;
				}
				record({ ...result.entry, ...base });
				if (result.refusal) return { block: true, reason: result.refusal };
				allow(call, event.toolCallId);
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				pi.logger.debug("decision-review-gate: check failed; let through", { tool: call.tool, error: message });
				if (base) {
					record({ at: new Date().toISOString(), outcome: "error", error: message, ...base });
					allow(call, event.toolCallId);
				}
			}
			return undefined;
		});
	};
}

export default function decisionReviewGate(pi: ExtensionAPI): void {
	createDecisionReviewGate()(pi);
}
