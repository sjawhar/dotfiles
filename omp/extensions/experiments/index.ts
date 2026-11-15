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
// latest such entry already says the same (a resumed session). Every feature but the text arms reads
// a new resolution at once. The text arms read it only once its entry is recorded (below), and a
// failed append there throws nothing. A handler that throws or rejects is logged through omp's
// logger and its feature stays off until the next resolution; the others keep running. The installer
// links only this file; its imports resolve from its real path.
// The three features of text-arms.ts run in both arms of a gate that is `on` or `random`, as the
// bash gate does, and read the arm per call. They register through a view of the same kind, so the
// error rule above holds for them too. A root session is a unit of their experiments when its file
// carries the `experiments` entry and at least one user prompt after it. A session that reaches that
// prompt gets an `experiments-exposure` entry in either arm. A Legion pane gets an
// `experiments-excluded` entry instead, and no text (text-arms.ts). A session with no such prompt gets
// neither. The join lists a session with no such prompt, and an excluded session, apart. A gate that
// is `off` runs none of their handlers. One rule holds for them whatever the gate: a text reaches the
// model only while the current arm of its feature is on, and only with its exposure entry. After
// their handlers, a scrub for each of them (scrubText of text-arms.ts) runs outside the gate and the
// guard. It removes the text of the feature from each request unless the session has a recorded
// resolution whose arm for the feature is on, the feature did not stop after a throw, and the
// feature's own permit holds (text-arms.ts: not a Legion pane, the text not withheld, and an exposure
// entry in the context or appended by this same request). A throw
// of a permit goes through the feature's logged failure path, and the scrub removes the text.
// The text arms, and only they, also follow gates.json as it is on disk while omp runs. At each
// before_agent_start and each context event, the extension looks at the mtime and the size of the
// gates.json beside this file's real path. When either changed since the last look, it reads and
// parses the file with the rules of resolveGates and the process environment, and draws the text
// arms again from the same root id. When their resolution changes, it appends an `experiments`
// entry, as at a resolution, so the join sees the arm in force. At a resolution and at such a change,
// the new resolution takes effect for the text arms only once that entry is recorded. A failed append
// keeps it proposed, is logged once, and is tried again at every later before_agent_start and context
// event. Until it is recorded the text arms do not run, and the scrubs remove their text. Each new
// proposal and each recorded resolution moves a count (`generation`), and a trigger made under an
// older count is stale (text-arms.ts). A file that cannot be read or parsed is logged
// once, and the text arms keep their last good gates. So an `off` push stops the text at the next
// request of every running session, and a gate that goes `random` assigns at the next user prompt.
// Every other feature keeps the gates it read at load.
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import bashGate from "./bash-gate";
import gateFile from "./gates.json" with { type: "json" };
import { ENTRY_TYPE, type ExperimentRecord, type Feature, type Gates, resolve, resolveGates, rootSessionId } from "./gates";
import judgeLog from "./judge-log";
import proactiveMemory from "./proactive-memory";
import rulesRefresh from "./rules-refresh";
import selfcompact from "./selfcompact";
import skillGate from "./skill-gate";
import { aaNoop, infra805Selfcheck, infra805Skill, type Permit, scrubText, type TextArm, type TextArmHandle } from "./text-arms";

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

/** gates.json beside this file's real path: the file the static import above reads at load. */
const GATES_PATH = join(import.meta.dir, "gates.json");

export default function experiments(pi: ExtensionAPI): void {
	bindExperiments(pi, GATES_PATH);
}

/** The extension, with the text arms following the gates in `gatesPath` while omp runs. The tests give it a temp copy. */
export function bindExperiments(pi: ExtensionAPI, gatesPath: string): void {
	const gates = resolveGates(gateFile, process.env);
	let current: ExperimentRecord | undefined;
	const stopped = new Set<Feature>();

	// The gates of the text arms, as gatesPath last read well, and the mtime and size of the version last looked at.
	let liveGates: Gates = gates;
	let liveVersion: string | undefined;
	/** Reads gatesPath again when its mtime or size changed since the last look. True when that gave new good gates. */
	const readLiveGates = (): boolean => {
		let version: string;
		try {
			const stat = statSync(gatesPath);
			version = `${stat.mtimeMs}:${stat.size}`;
		} catch {
			version = "unreadable";
		}
		if (version === liveVersion) return false;
		liveVersion = version;
		try {
			liveGates = resolveGates(JSON.parse(readFileSync(gatesPath, "utf8")), process.env);
			return true;
		} catch (error) {
			pi.logger.error(`experiments: the text arms cannot read the gates in ${gatesPath}, so they keep their last good gates`, {
				error: error instanceof Error ? error.message : String(error),
			});
			return false;
		}
	};
	/** `record` with the text arms drawn again, from the same root id, under the live gates. */
	const withLiveTextArms = (record: ExperimentRecord): ExperimentRecord => {
		const live = resolve(liveGates, record.rootSessionId).features;
		const features = { ...record.features };
		for (const [feature] of TEXT_ARMS) features[feature] = live[feature];
		return { ...record, features };
	};

	// The latest `experiments` record of this session's file, as this binding knows it.
	let lastRecorded: unknown;
	// A resolution of the text arms whose `experiments` entry is not recorded yet, and whether its failed append was logged.
	let proposed: ExperimentRecord | undefined;
	let proposalLogged = false;
	// Counts each change of the text arms' state in force: each new proposal and each recorded resolution, through `off`
	// included. A trigger made under an older count is stale (text-arms.ts).
	let generation = 0;
	/** Appends `proposed`. On success the text arms run under it. On a throw it stays proposed, is logged once, and is tried again at the next event. Never throws. */
	const recordProposed = () => {
		if (proposed === undefined) return;
		try {
			pi.appendEntry(ENTRY_TYPE, proposed);
		} catch (error) {
			if (!proposalLogged) {
				proposalLogged = true;
				pi.logger.error("experiments: the text arms cannot record their new resolution, so they send no text until it is recorded", {
					error: error instanceof Error ? error.message : String(error),
				});
			}
			return;
		}
		lastRecorded = proposed;
		proposed = undefined;
		proposalLogged = false;
		generation++;
	};
	/**
	 * Every other feature reads the new resolution at once, as before. The text arms run under it only once its
	 * `experiments` entry is recorded, or the session's latest entry already says the same.
	 */
	const resolveSession = (_event: unknown, ctx: Ctx) => {
		const sm = ctx.sessionManager;
		readLiveGates();
		current = withLiveTextArms(resolve(gates, rootSessionId(sm.getSessionFile(), sm.getSessionId())));
		stopped.clear();
		proposalLogged = false;
		lastRecorded = sm.getEntries().findLast(e => e.type === "custom" && e.customType === ENTRY_TYPE)?.data;
		proposed = Bun.deepEquals(lastRecorded, current) ? undefined : current;
		generation++;
		recordProposed();
	};
	pi.on("session_start", resolveSession);
	pi.on("session_switch", resolveSession);
	pi.on("session_branch", resolveSession);
	/**
	 * Registered before the text arms, so each of their handlers reads the arm in force. A changed resolution takes
	 * effect for the text arms only once its `experiments` entry is recorded. Until then they do not run, and their
	 * scrubs remove their text.
	 */
	const followGates = () => {
		if (current === undefined) return;
		if (readLiveGates()) {
			const next = withLiveTextArms(current);
			if (!Bun.deepEquals(next, current)) {
				current = next;
				proposed = Bun.deepEquals(next, lastRecorded) ? undefined : next;
				generation++;
			}
		}
		recordProposed();
	};
	pi.on("before_agent_start", followGates);
	pi.on("context", followGates);
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
	// A text arm runs only after a resolution, and while no resolution waits to be recorded, so its reader always finds the recorded one.
	const textRuns = (feature: Feature) => current !== undefined && proposed === undefined && current.features[feature].gate !== "off";
	const handles: Array<[Feature, TextArmHandle]> = TEXT_ARMS.map(([feature, register]) => [
		feature,
		register(
			view(feature, () => textRuns(feature)),
			() => (current as ExperimentRecord).features[feature],
			() => generation,
		),
	]);
	// Registered last, outside the gate and the guard, so each runs after every text arm's own handlers, in every request.
	// A throw of a permit goes through the feature's logged failure path, and the scrub removes the text.
	for (const [feature, { permit, text }] of handles) {
		const on = (ctx: Parameters<Permit>[0]) => current !== undefined && proposed === undefined && current.features[feature].on && !stopped.has(feature) && permit(ctx);
		pi.on("context", scrubText(feature, text, on, error => fail(feature, "the context scrub", error)) as never);
	}
}
