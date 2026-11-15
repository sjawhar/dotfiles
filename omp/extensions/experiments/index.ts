// The experiments extension: one extension holding the gates of ten experimental session features
// (gates.ts says how a gate is read and drawn), nine of them run from here.
//
//   selfcompact       compacts early at a task boundary when the model says its history can be
//                     summarized (selfcompact.ts)
//   proactive_memory  a side agent keeps a knowledge bank and slips a reminder into the next
//                     request when it judges one useful (proactive-memory-core.ts)
//   skill_gate        refuses `gh pr create` until skill://opening-a-pr is read, and `jj git push`
//                     until skill://using-jj is read (skill-gate.ts)
//   judge_log         rates every subagent dispatch with the `@judgeLog` model role and appends the
//                     rating to ~/.omp/judge/ratings.jsonl, routing on nothing (judge-log-core.ts)
//   rules_refresh     before each prompt, quotes context-file paragraphs that changed on disk since
//                     the session loaded its system prompt (rules-refresh.ts)
//   askgate_block     pi-envoy's AskGate refuses a revised Dispatch write instead of letting it run
//                     with a warning, when OMP_ASKGATE is unset; AskGate reads the arm from this
//                     extension's latest `experiments` entry, so nothing registers here
//   bash_gate_leading omp's bash interceptor, run from here in both arms: on, a read, grep or glob
//                     rule blocks only the command's leading segment; off, every segment, as omp
//                     does (bash-gate-core.ts). Its handler is not gated: the arm is read per call
//   aa_noop           the feature of the A/A test. It changes nothing in either arm, and records the
//                     exposure at the point where the two below put their text (text-arms.ts)
//   infra805_skill    on, puts the following-best-practices skill of cybermonklabs/legion#2616 into
//                     the context of a top-level session. Both arms record the exposure (text-arms.ts)
//   infra805_selfcheck on, puts the one-paragraph self-ask instruction of dispatch://INFRA-805 there.
//                     Both arms record the exposure (text-arms.ts)
//
// A bad gate throws here, so omp loads none of it and says why. Each feature registers its
// handlers once, through a view of `pi` whose `on` runs a handler only while that feature is on
// for the current session; the gates resolve at session_start, and again at session_switch and
// session_branch, which move this binding to another session file. Each resolution appends one
// `experiments` custom entry naming every feature's gate, draw and outcome, unless the session's
// latest such entry already says the same (a resumed session). A handler that throws or rejects
// is logged through omp's logger and its feature stays off until the next resolution; the others
// keep running. The installer links only this file; its imports resolve from its real path.
// The three features of text-arms.ts run in both arms of a gate that is `on` or `random`, as the
// bash gate does, and read the arm per call. They register through a view of the same kind, so the
// error rule above holds for them too. In either arm they append an `experiments-exposure` entry, so
// the join of assignment, exposure and outcome finds every assigned session. A gate that is `off`
// runs none of their handlers.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import bashGate from "./bash-gate";
import gateFile from "./gates.json" with { type: "json" };
import { ENTRY_TYPE, type ExperimentRecord, type Feature, resolve, resolveGates, rootSessionId } from "./gates";
import judgeLog from "./judge-log";
import proactiveMemory from "./proactive-memory";
import rulesRefresh from "./rules-refresh";
import selfcompact from "./selfcompact";
import skillGate from "./skill-gate";
import { aaNoop, infra805Selfcheck, infra805Skill, type TextArm } from "./text-arms";

/** Registration order is handler order within an event: SelfCompact reads the request's tail before the memory agent appends its reminder, and the context line goes on whatever is last. */
const REGISTRATIONS: ReadonlyArray<[Feature, (pi: ExtensionAPI) => void]> = [
	["selfcompact", selfcompact],
	["proactive_memory", proactiveMemory],
	["skill_gate", skillGate],
	["judge_log", judgeLog],
	["rules_refresh", rulesRefresh],
];
/** The features of text-arms.ts, registered after the others. They run in both arms of a gate that is `on` or `random`, and read the arm per call. */
const TEXT_ARMS: ReadonlyArray<[Feature, TextArm]> = [
	["aa_noop", aaNoop],
	["infra805_skill", infra805Skill],
	["infra805_selfcheck", infra805Selfcheck],
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
	// Before the first resolution a call gets the off arm, which is omp's own behavior.
	bashGate(pi, () => current?.features.bash_gate_leading.on === true);

	const fail = (feature: Feature, event: string, error: unknown) => {
		stopped.add(feature);
		pi.logger.error(`experiments: ${feature} failed in ${event}; it stays off for the rest of this session`, {
			error: error instanceof Error ? (error.stack ?? error.message) : String(error),
		});
		return undefined;
	};
	/** Runs `handler` only while `runs()` holds and `feature` has not failed in this session. A throw or a rejection stops `feature` until the next resolution. */
	const guarded = (feature: Feature, event: string, handler: Handler, runs: () => boolean): Handler => (e, ctx) => {
		if (!runs() || stopped.has(feature)) return undefined;
		try {
			const result = handler(e, ctx);
			return result instanceof Promise ? result.catch(error => fail(feature, event, error)) : result;
		} catch (error) {
			return fail(feature, event, error);
		}
	};
	/** `pi` as `feature` sees it: its `on` registers a handler that `guarded` wraps. */
	const view = (feature: Feature, runs: () => boolean) =>
		new Proxy(pi, {
			get(target, prop) {
				if (prop === "on") return (event: string, handler: Handler) => target.on(event as "session_start", guarded(feature, event, handler, runs) as never);
				const value = Reflect.get(target, prop, target);
				return typeof value === "function" ? value.bind(target) : value;
			},
		});
	for (const [feature, register] of REGISTRATIONS) register(view(feature, () => current?.features[feature].on === true));
	// A text arm runs only after a resolution, so its reader always finds one.
	for (const [feature, register] of TEXT_ARMS) {
		register(
			view(feature, () => current !== undefined && current.features[feature].gate !== "off"),
			() => (current as ExperimentRecord).features[feature],
		);
	}
}
