// The experiments extension: one extension holding five experimental session features, each
// behind its own gate (gates.ts says how a gate is read and drawn).
//
//   selfcompact       compacts early at a task boundary when the model says its history can be
//                     summarized (selfcompact.ts)
//   proactive_memory  a side agent keeps a knowledge bank and slips a reminder into the next
//                     request when it judges one useful (proactive-memory-core.ts)
//   context_line      appends "[Token usage: used/window]" to every request (context-line.ts)
//   skill_gate        refuses `gh pr create` until skill://opening-a-pr is read, and `jj git push`
//                     until skill://using-jj is read (skill-gate.ts)
//   judge_log         rates every subagent dispatch with the `@judgeLog` model role and appends the
//                     rating to ~/.omp/judge/ratings.jsonl, routing on nothing (judge-log-core.ts)
//
// A bad gate throws here, so omp loads none of it and says why. Each feature registers its
// handlers once, through a view of `pi` whose `on` runs a handler only while that feature is on
// for the current session; the gates resolve at session_start, and again at session_switch and
// session_branch, which move this binding to another session file. Each resolution appends one
// `experiments` custom entry naming every feature's gate, draw and outcome, unless the session's
// latest such entry already says the same (a resumed session). A handler that throws or rejects
// is logged through omp's logger and its feature stays off until the next resolution; the others
// keep running. The installer links only this file; its imports resolve from its real path.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import contextLine from "./context-line";
import gateFile from "./gates.json" with { type: "json" };
import { ENTRY_TYPE, type ExperimentRecord, type Feature, resolve, resolveGates, rootSessionId } from "./gates";
import judgeLog from "./judge-log";
import proactiveMemory from "./proactive-memory";
import selfcompact from "./selfcompact";
import skillGate from "./skill-gate";

/** Registration order is handler order within an event: SelfCompact reads the request's tail before the memory agent appends its reminder, and the context line goes on whatever is last. */
const REGISTRATIONS: ReadonlyArray<[Feature, (pi: ExtensionAPI) => void]> = [
	["selfcompact", selfcompact],
	["proactive_memory", proactiveMemory],
	["context_line", contextLine],
	["skill_gate", skillGate],
	["judge_log", judgeLog],
];

type Handler = (event: unknown, ctx: unknown) => unknown;
type Ctx = {
	sessionManager: {
		getSessionFile(): string | undefined;
		getSessionId(): string;
		getEntries(): ReadonlyArray<{ type: string; customType?: string; data?: unknown }>;
	};
};

export default function experiments(pi: ExtensionAPI): void {
	const gates = resolveGates(gateFile, process.env);
	let current: ExperimentRecord | undefined;
	const stopped = new Set<Feature>();

	const resolveSession = (_event: unknown, ctx: Ctx) => {
		const sm = ctx.sessionManager;
		current = resolve(gates, rootSessionId(sm.getSessionFile(), sm.getSessionId()));
		stopped.clear();
		const entries = sm.getEntries();
		const last = entries.findLast(e => e.type === "custom" && e.customType === ENTRY_TYPE);
		if (!Bun.deepEquals(last?.data, current)) pi.appendEntry(ENTRY_TYPE, current);
	};
	pi.on("session_start", resolveSession);
	pi.on("session_switch", resolveSession);
	pi.on("session_branch", resolveSession);

	const fail = (feature: Feature, event: string, error: unknown) => {
		stopped.add(feature);
		pi.logger.error(`experiments: ${feature} failed in ${event}; it stays off for the rest of this session`, {
			error: error instanceof Error ? (error.stack ?? error.message) : String(error),
		});
		return undefined;
	};
	const gated = (feature: Feature, event: string, handler: Handler): Handler => (e, ctx) => {
		if (!current?.features[feature].on || stopped.has(feature)) return undefined;
		try {
			const result = handler(e, ctx);
			return result instanceof Promise ? result.catch(error => fail(feature, event, error)) : result;
		} catch (error) {
			return fail(feature, event, error);
		}
	};
	for (const [feature, register] of REGISTRATIONS) {
		const view = new Proxy(pi, {
			get(target, prop) {
				if (prop === "on") return (event: string, handler: Handler) => target.on(event as "session_start", gated(feature, event, handler) as never);
				const value = Reflect.get(target, prop, target);
				return typeof value === "function" ? value.bind(target) : value;
			},
		});
		register(view);
	}
}
