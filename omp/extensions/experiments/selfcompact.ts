// SelfCompact as an extension: ask the model, at a request boundary, whether its
// history has reached a state that a compaction summary can carry, and compact
// early when it says so. The rubric and its four conditions are the paper's
// (Li et al., "Self-Compacting Language Model Agents", arXiv:2606.23525,
// Appendix B) with a coding agent's nouns; `selfcompact.md` holds it verbatim.
//
// The hook is `context`, fired before each provider request and awaited by the
// agent loop, so the decision lands on the boundary: no tool is running when a
// fire aborts the turn in flight. (`turn_end` is not awaited by the loop, which
// only awaits its own onTurnEnd hook; a probe there would race the next turn.)
// The host also runs `context` for every side turn (`runEphemeralTurn`, /btw,
// /omfg, the idle recap) and for handoff generation, whose contexts end in their
// own prompt; a fire there would abort a running tool, compact an idle session
// or replace a handoff. So only a request whose context ends in tool results — a
// run continuing after its tool calls, the paper's "ended with tool calls" — is
// counted or probed. Deterministic gates run before any model call: at least
// three assistant turns are in the context about to be sent, the context holds
// at least 40 000 tokens, this window has not fired, has not failed to fire
// twice and has not had three failed probes, and at least two requests have
// passed since the last probe. A window ends at every committed compaction the
// extension did not fire and at every successful shake (a successful shake as
// `compaction-reminder.ts` defines it); the fire's own compaction does not
// reopen it, so a window holds at most one self-summary (the paper's cap on
// summaries, kept per window).
//
// The probe is one ephemeral side turn against the live context: nothing is
// appended to history, the tool catalog is sent so the prompt cache holds, and
// the reply is parsed conservatively — anything but the exact four lines is
// CONTINUE. The probe has its own 20 s deadline, inside the host's 30 s handler
// budget: a probe that fails or runs past it is CONTINUE, a verdict that lands
// after it never fires, and neither leaves a host "Extension error" line. The
// side turn runs the context hooks itself, so this handler is
// re-entered while probing; one phase (idle, probing, firing) makes that
// re-entry, and every request until a fire's promise settles, a no-op. A fire
// is `ctx.compact()` with no mode, so the operator's `compaction.methodOrder`
// still decides how; it is never awaited, because a compaction outlives the
// 30 s handler budget. A fire that settles without `onComplete` is recorded as
// failed, whether its promise rejects (print and RPC rethrow; a detached
// rejection is fatal to the session) or resolves with neither callback run (the
// TUI swallows the error the manual path throws before `onError`). It runs
// only in the top-level session, and only while the experiments extension's
// `selfcompact` gate is on for it (index.ts). Every probe (`selfcompact-probe`),
// fire (`selfcompact-fire`) and handler failure outside a probe
// (`selfcompact-error`) is recorded as a custom session entry. Each ok fire's
// record carries the compaction summary. scripts/selfcompact-sessions reads
// these records from real sessions.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import rubric from "./selfcompact.md" with { type: "text" };

const RUBRIC = rubric.trim();
const MIN_TURNS = 3;
const MIN_TOKENS = 40_000;
const PROBE_PERIOD = 2;
const MAX_FIRES_PER_WINDOW = 1;
const MAX_FAILED_FIRES_PER_WINDOW = 2;
const MAX_FAILED_PROBES_PER_WINDOW = 3;
const PROBE_MAX_TOKENS = 512;
/** The probe's own deadline, well inside the host's 30 s handler budget (runner.ts:120), so a slow probe never becomes a host "Extension error" line. */
const PROBE_TIMEOUT_MS = 20_000;
const PROBE_DEADLINE = "probe deadline";
const LABELS = ["C1", "C2", "C3", "N1"] as const;
const LINE = /^(C1|C2|C3|N1):\s*([YN])\b/;
/** The text of the two plain errors the host throws before inference when a model cannot keep an output cap (agent-session.ts:10136 budget thinking, :10148 no output limit). */
const CAP_REJECTED = /maxTokens for ephemeral turns/;

type Label = (typeof LABELS)[number];
type Answers = Record<Label, "Y" | "N">;
type Verdict = "compress" | "continue";
type Usage = { input: number; output: number; cacheRead: number; cacheWrite: number; cost: { total: number } };
type ProbeResult = {
	replyText: string;
	assistantMessage: { provider: string; model: string; usage: Usage; stopReason: string };
};
type ProbeOptions = { promptText: string; maxTokens?: number; conversationKey: string; signal: AbortSignal };
type CompactionResult = { summary: string; firstKeptEntryId: string; tokensBefore: number };
type CompactOptions = { onComplete?: (result: CompactionResult) => void; onError?: (error: Error) => void };
type Ctx = {
	agent: { kind: "main" | "sub" };
	getContextUsage: () => { tokens: number; contextWindow: number; percent: number } | undefined;
	runEphemeralTurn?: (options: ProbeOptions) => Promise<ProbeResult>;
	compact: (options: CompactOptions) => Promise<void>;
};
type ContextEvent = { messages: Array<{ role: string }> };
type ShakeEnd = { action: string; aborted: boolean; skipped?: boolean; errorMessage?: string };

/** The paper's fire rule over the paper's output format; anything malformed is CONTINUE. */
export function parseRubric(reply: string): { verdict: Verdict; answers?: Answers; reason?: string } {
	const lines = reply.split("\n").map(line => line.trim()).filter(line => line.length > 0);
	if (lines.length !== 4) return { verdict: "continue", reason: `expected four lines, got ${lines.length}` };
	const answers = {} as Answers;
	for (let i = 0; i < LABELS.length; i++) {
		const match = LINE.exec(lines[i]);
		if (!match || match[1] !== LABELS[i]) return { verdict: "continue", reason: `line ${i + 1} is not ${LABELS[i]} (order or format)` };
		answers[LABELS[i]] = match[2] as "Y" | "N";
	}
	const compress = answers.C1 === "Y" && answers.C2 === "Y" && answers.C3 === "Y" && answers.N1 === "N";
	return { verdict: compress ? "compress" : "continue", answers };
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

export default function selfcompact(pi: ExtensionAPI): void {
	let requests = 0;
	let lastProbeRequest = -Infinity;
	let fires = 0;
	let failedFires = 0;
	let failedProbes = 0;
	let conversation = 0;
	/** "probing" from a probe's start to its verdict; "firing" from a fire's `ctx.compact()` call until that promise settles, so a `session_compact` in between is the fire's own. */
	let phase: "idle" | "probing" | "firing" = "idle";

	const record = (type: "selfcompact-probe" | "selfcompact-fire" | "selfcompact-error", data: Record<string, unknown>) => {
		pi.appendEntry(type, { v: 1, ...data });
	};
	const resetWindow = () => {
		fires = 0;
		failedFires = 0;
		failedProbes = 0;
		lastProbeRequest = -Infinity;
	};

	/** One rubric side turn, asked once more without the output cap when the model rejects the cap. Never throws; `capped` is true only when the capped call produced the result. */
	const probe = async (run: NonNullable<Ctx["runEphemeralTurn"]>, signal: AbortSignal): Promise<{ result?: ProbeResult; capped: boolean; error?: string }> => {
		try {
			return { result: await run({ promptText: RUBRIC, maxTokens: PROBE_MAX_TOKENS, conversationKey: `selfcompact:${conversation}`, signal }), capped: true };
		} catch (error) {
			conversation++; // the host asks for a fresh key after a failed or cancelled side turn (agent-session-types.ts:534)
			if (!CAP_REJECTED.test(message(error))) return { capped: false, error: message(error) };
		}
		try {
			return { result: await run({ promptText: RUBRIC, conversationKey: `selfcompact:${conversation}`, signal }), capped: false };
		} catch (error) {
			conversation++;
			return { capped: false, error: message(error) };
		}
	};

	const fire = (ctx: Ctx, turn: number) => {
		phase = "firing";
		let settled = false;
		/** The first outcome wins: `onError` is followed by a rethrow on the manual path. */
		const settle = (data: { ok: true; tokensBefore: number; firstKeptEntryId: string; summary: string } | { ok: false; error: string }) => {
			if (settled) return;
			settled = true;
			if (data.ok) fires++;
			else failedFires++;
			record("selfcompact-fire", { turn, ...data });
		};
		const failed = (error: unknown) => settle({ ok: false, error: message(error) });
		void ctx
			.compact({
				onComplete: ({ tokensBefore, firstKeptEntryId, summary }) => settle({ ok: true, tokensBefore, firstKeptEntryId, summary }),
				onError: failed,
			})
			// A rejection before the manual path's try block ("Compaction already in
			// progress") never reaches `onError`: print and RPC rethrow it, the TUI's
			// executeCompaction shows it and resolves. Either settle, with neither
			// callback run, is a failed fire.
			.then(() => failed(new Error("compaction settled without onComplete or onError")), failed)
			.finally(() => {
				phase = "idle";
			});
	};

	pi.on("context", async (event: ContextEvent, ctx: Ctx) => {
		if (phase !== "idle") return;
		try {
			if (ctx.agent.kind !== "main") return;
			if (event.messages.at(-1)?.role !== "toolResult") return; // side turns, handoffs and steering end in their own prompt
			requests++;
			const turns = event.messages.filter(m => m.role === "assistant").length;
			if (turns < MIN_TURNS) return;
			const usage = ctx.getContextUsage();
			if (!usage || usage.tokens < MIN_TOKENS) return;
			if (fires >= MAX_FIRES_PER_WINDOW || failedFires >= MAX_FAILED_FIRES_PER_WINDOW || failedProbes >= MAX_FAILED_PROBES_PER_WINDOW) return;
			if (requests - lastProbeRequest < PROBE_PERIOD) return;
			if (!ctx.runEphemeralTurn) return;
			lastProbeRequest = requests;
			phase = "probing";
			const started = performance.now();
			// The host combines this signal with the handler's own. Past it, the outcome is the deadline whatever
			// the side turn returned: a late verdict is recorded, with its cost, but never fires.
			const signal = AbortSignal.timeout(PROBE_TIMEOUT_MS);
			const probed = await probe(ctx.runEphemeralTurn, signal);
			const { result, capped, error } = signal.aborted ? { ...probed, error: PROBE_DEADLINE } : probed;
			if (error) failedProbes++;
			const outcome: { verdict: Verdict; answers?: Answers; reason?: string; error?: string } =
				result && !error ? parseRubric(result.replyText) : { verdict: "continue", error };
			const reply = result?.assistantMessage;
			const u = reply?.usage;
			record("selfcompact-probe", {
				turn: turns,
				request: requests,
				tokens: usage.tokens,
				contextWindow: usage.contextWindow,
				percent: usage.percent,
				...outcome,
				probeMs: Math.round(performance.now() - started),
				capped,
				stopReason: reply?.stopReason,
				usage: u && { input: u.input, output: u.output, cacheRead: u.cacheRead, cacheWrite: u.cacheWrite },
				// The host types cost as required; if a provider ever omits it, the record carries none.
				// A fire never depends on cost bookkeeping.
				cost: u?.cost?.total,
				model: reply?.provider && reply?.model ? `${reply.provider}/${reply.model}` : undefined,
			});
			if (outcome.verdict === "compress") fire(ctx, turns);
			else phase = "idle";
		} catch (error) {
			// Fail open: a broken handler must never cost the session a request. Not a
			// probe record: no side turn produced a verdict here.
			phase = "idle";
			record("selfcompact-error", { request: requests, error: message(error) });
			pi.logger.warn("selfcompact: context handler failed", { error: message(error) });
		}
	});
	// The host emits the fire's own session_compact while ctx.compact() is pending, before
	// onComplete (session-maintenance.ts commits the entry, then calls onComplete).
	pi.on("session_compact", () => {
		if (phase !== "firing") resetWindow();
	});
	pi.on("auto_compaction_end", (event: ShakeEnd) => {
		if (event.action === "shake" && !event.aborted && !event.skipped && event.errorMessage === undefined) resetWindow();
	});
	// /new, /resume and /fork (session_switch), a branch and a tree move keep this
	// binding but start another conversation, so they start a fresh window
	// (docs/extensions.md: rebuild state on session_branch and session_tree). A fire
	// in flight settles on its own: the switch's abort cancels it and onError runs.
	const newSession = () => {
		requests = 0;
		resetWindow();
	};
	pi.on("session_switch", newSession);
	pi.on("session_branch", newSession);
	pi.on("session_tree", newSession);
}
