// The gates of the experiments extension: which features exist, how a gate is read, and how a
// `random` gate is drawn. Pure, so the draw and the refusals are tested without omp.
//
// A gate is `on`, `off` or `random`. gates.json holds every feature's default; an environment
// variable `OMP_EXPERIMENT_<FEATURE>` (the feature's name upper-cased) overrides one for a launch.
// Anything else refuses the load, naming what was wrong: a variable whose value is not a gate, a
// variable or a gate-file key naming no feature, a feature the gate file leaves out.
//
// A `random` gate is one bit of sha256(<root session id> NUL <feature>), so a draw is stable for a
// session, independent across features, and the same in every subagent of that session. The root
// id is the first `Z_<uuid>` in the session file's path: a root's transcript is named
// `<timestamp>Z_<uuid>.jsonl` and its subagents' transcripts sit under the directory of the same
// name. A session with no file (--no-session) draws from its own id.
import { createHash } from "node:crypto";

export const FEATURES = ["selfcompact", "proactive_memory", "skill_gate", "judge_log", "rules_refresh", "askgate_block", "bash_gate_leading", "aa_noop", "infra805_skill", "infra805_selfcheck"] as const;
export type Feature = (typeof FEATURES)[number];
export const GATES = ["on", "off", "random"] as const;
export type Gate = (typeof GATES)[number];
export type Gates = Record<Feature, Gate>;
/** One feature's line in the session's record: its gate, what the hash drew, and whether it runs. */
export type Resolution = { gate: Gate; draw: "on" | "off"; on: boolean };
export type ExperimentRecord = { v: 1; rootSessionId: string; features: Record<Feature, Resolution> };

export const ENTRY_TYPE = "experiments";
/** The custom type of the entry a feature of text-arms.ts appends, in either arm, at the point where the treatment reaches the session in the on arm. */
export const EXPOSURE_ENTRY_TYPE = "experiments-exposure";
/** One exposure: the feature, the arm of the session, its gate (`off` writes no entry), the root session id (rootIdFromPath, else the session's own id) and the time (ISO 8601, UTC). */
export type ExposureRecord = { v: 1; feature: Feature; arm: "on" | "off"; gate: Exclude<Gate, "off">; rootSessionId: string; at: string };
/** The custom type of the entry a feature of text-arms.ts appends, in place of any text or exposure entry, in a session that is outside its experiments. */
export const EXCLUDED_ENTRY_TYPE = "experiments-excluded";
/** One exclusion: the feature, the reason (`legion_pane`: a Legion pane), the root session id (rootIdFromPath, else the session's own id) and the time (ISO 8601, UTC). */
export type ExcludedRecord = { v: 1; feature: Feature; reason: "legion_pane"; rootSessionId: string; at: string };
const ENV_PREFIX = "OMP_EXPERIMENT_";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
/** The uuid after `Z_` in a session file path (the root's id for its own file and its subagents' files alike). */
const ROOT_IN_PATH = new RegExp(`Z_(${UUID})`);
const BARE_ID = new RegExp(`^${UUID}$`);

const isFeature = (name: string): name is Feature => (FEATURES as readonly string[]).includes(name);
export const isGate = (value: unknown): value is Gate => typeof value === "string" && (GATES as readonly string[]).includes(value);

export const envName = (feature: Feature) => `${ENV_PREFIX}${feature.toUpperCase()}`;

/** Every feature's gate: the gate file's, then each `OMP_EXPERIMENT_<FEATURE>` set in `env`. Throws, naming the cause, on anything else. */
export function resolveGates(file: unknown, env: Record<string, string | undefined>): Gates {
	if (typeof file !== "object" || file === null || Array.isArray(file)) {
		throw new Error(`experiments: gates.json must map each feature (${FEATURES.join(", ")}) to on, off or random`);
	}
	const gates = {} as Gates;
	for (const [name, value] of Object.entries(file)) {
		if (!isFeature(name)) throw new Error(`experiments: gates.json names unknown feature "${name}" (features: ${FEATURES.join(", ")})`);
		if (!isGate(value)) throw new Error(`experiments: gates.json gives feature "${name}" the gate ${JSON.stringify(value)}; a gate is on, off or random`);
		gates[name] = value;
	}
	for (const feature of FEATURES) {
		if (!Object.hasOwn(gates, feature)) throw new Error(`experiments: gates.json has no gate for feature "${feature}"`);
	}
	for (const [name, value] of Object.entries(env)) {
		if (!name.startsWith(ENV_PREFIX) || value === undefined) continue;
		const feature = name.slice(ENV_PREFIX.length).toLowerCase();
		if (!isFeature(feature) || envName(feature) !== name) {
			throw new Error(`experiments: ${name} names no feature (variables: ${FEATURES.map(envName).join(", ")})`);
		}
		if (!isGate(value)) throw new Error(`experiments: ${name} must be on, off or random, got ${JSON.stringify(value)}`);
		gates[feature] = value;
	}
	return gates;
}

/** The root session's id in a session file path or a bare session id; undefined when it holds none. */
export function rootIdFromPath(sessionPath: string | undefined): string | undefined {
	if (!sessionPath) return undefined;
	if (BARE_ID.test(sessionPath)) return sessionPath;
	return ROOT_IN_PATH.exec(sessionPath)?.[1];
}

/** The id a session draws from: its root's, read from its file's path, or its own when it has no file. */
export const rootSessionId = (sessionFile: string | undefined, sessionId: string) => rootIdFromPath(sessionFile) ?? sessionId;

/** The random draw of `feature` for the root session `rootId`. */
export function draw(rootId: string, feature: Feature): boolean {
	return (createHash("sha256").update(`${rootId}\0${feature}`).digest()[0] & 1) === 1;
}

/** What every feature resolves to in the session whose root is `rootId`. */
export function resolve(gates: Gates, rootId: string): ExperimentRecord {
	const features = {} as Record<Feature, Resolution>;
	for (const feature of FEATURES) {
		const drawn = draw(rootId, feature);
		const gate = gates[feature];
		features[feature] = { gate, draw: drawn ? "on" : "off", on: gate === "on" || (gate === "random" && drawn) };
	}
	return { v: 1, rootSessionId: rootId, features };
}
