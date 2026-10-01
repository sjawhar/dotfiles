// The judge-difficulty log: every subagent dispatch is rated by the `@judge` model role
// (`modelRoles.judge` in config.yml) with the rubric in judge-rating.md, and each rating is appended
// as one JSON line to ~/.omp/judge/ratings.jsonl. Nothing routes on the rating. It runs in
// top-level sessions and subagents alike, while the experiments extension's `judge_log` gate is on
// (index.ts); judge-log.ts binds the fork's completion and the log file into it, so this file runs
// under `bun test` with fakes.
//
// A dispatch is a `task` call, and each item of a batch is rated on its own, with the batch's shared
// context prefixed to its task. The judge sees that assignment text only, never the agent, model or
// effort the call names. One line per rating: { at, sessionFile, toolCallId, itemIndex, assignment
// (its first 500 characters), difficulty, spec, axis, risk, judgeModel }.
//
// The rating is made off the dispatch path: the `tool_execution_start` handler starts it detached and
// returns at once, so neither the tool nor the session's event delivery waits on the judge. A judge
// failure (no `@judge` role, a provider error, the 20 s timeout, an answer that is not a rating, a
// failed write) is logged through omp's logger and writes nothing. A rating still in flight when the
// process exits is lost.
import ratingTemplate from "./judge-rating.md" with { type: "text" };

export const JUDGE_ROLE = "@judge";
export const RATING_SYSTEM = "You rate a work assignment that is about to be given to an autonomous coding agent. You never do the work and you never ask questions. Output one JSON object, nothing else.";
const ASSIGNMENT_CAP = 12_000;
const LOGGED_CHARS = 500;
const JUDGE_TIMEOUT_MS = 20_000;
const AXES: Record<string, true> = { search: true, reading: true, editing: true, reasoning: true, judgement: true, writing: true };

export type ModelRef = { provider: string; id: string };
export type Completion = { text: string; error?: string };
export type CompleteRequest = { ctx: unknown; model: ModelRef; system: string; user: string; sessionId: string; signal: AbortSignal };
/** `append` writes one line to the log. */
export type Deps = { complete: (req: CompleteRequest) => Promise<Completion>; append: (line: string) => Promise<void>; now: () => number };
/** Minimal structural subset of the fork's ExtensionAPI the core needs; the entry binds the real one. */
export interface Pi {
	on: (event: string, handler: (event: never, ctx: never) => unknown) => void;
	logger: { warn: (msg: string, meta?: unknown) => void };
}
type Ctx = {
	models: { resolve: (spec: string) => ModelRef | undefined };
	sessionManager: { getSessionFile: () => string | undefined; getSessionId: () => string };
};
type StartEvent = { toolCallId: string; toolName: string; args: unknown };
export type Rating = { difficulty: number; spec: number; axis: string; risk: string };
export type RatingLine = { at: string; sessionFile: string; toolCallId: string; itemIndex: number; assignment: string } & Rating & { judgeModel: string };

/** `text` cut to `cap` characters by eliding its middle. */
export function clip(text: string, cap: number): string {
	if (text.length <= cap) return text;
	const half = Math.floor((cap - 48) / 2);
	return `${text.slice(0, half)}\n\n[... ${text.length - 2 * half} characters elided ...]\n\n${text.slice(-half)}`;
}

/** The assignment of each item a `task` call dispatches, in item order: a batch's context prefixed to each task. */
export function assignmentsOf(args: unknown): string[] {
	const p = (args ?? {}) as { task?: string; context?: string; tasks?: Array<{ task?: string }> };
	if (!Array.isArray(p.tasks)) return [(p.task ?? "").trim()];
	const context = (p.context ?? "").trim();
	return p.tasks.map(item => [context, (item.task ?? "").trim()].filter(Boolean).join("\n\n"));
}

const inRange = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 5;

/** The rating in the judge's answer (the JSON from its first `{` to its last `}`), every field present and in range; undefined otherwise. */
export function parseRating(text: string): Rating | undefined {
	const match = text.match(/\{[\s\S]*\}/);
	if (!match) return undefined;
	let o: Record<string, unknown>;
	try {
		o = JSON.parse(match[0]);
	} catch {
		return undefined;
	}
	if (!o || typeof o !== "object" || !inRange(o.difficulty) || !inRange(o.spec) || typeof o.axis !== "string" || !Object.hasOwn(AXES, o.axis) || typeof o.risk !== "string") return undefined;
	return { difficulty: o.difficulty, spec: o.spec, axis: o.axis, risk: o.risk.slice(0, 200) };
}

export function createJudgeLog(deps: Deps) {
	async function rate(ctx: Ctx, sessionFile: string, toolCallId: string, itemIndex: number, assignment: string): Promise<void> {
		const model = ctx.models.resolve(JUDGE_ROLE);
		if (!model) throw new Error(`role ${JUDGE_ROLE} resolves to no model`);
		// A replacer function, so a `$&` or `$'` in the assignment reaches the judge as written.
		const user = ratingTemplate.replace("{{assignment}}", () => clip(assignment, ASSIGNMENT_CAP));
		const answer = await deps.complete({ ctx, model, system: RATING_SYSTEM, user, sessionId: ctx.sessionManager.getSessionId(), signal: AbortSignal.timeout(JUDGE_TIMEOUT_MS) });
		if (answer.error !== undefined) throw new Error(answer.error);
		const rating = parseRating(answer.text);
		if (!rating) throw new Error(`the answer is not a rating: ${JSON.stringify(answer.text.slice(0, 500))}`);
		const line: RatingLine = {
			at: new Date(deps.now()).toISOString(),
			sessionFile,
			toolCallId,
			itemIndex,
			assignment: assignment.slice(0, LOGGED_CHARS),
			...rating,
			judgeModel: `${model.provider}/${model.id}`,
		};
		await deps.append(`${JSON.stringify(line)}\n`);
	}

	return function judgeLog(pi: Pi): void {
		pi.on("tool_execution_start", (event: StartEvent, ctx: Ctx) => {
			if (event.toolName !== "task") return undefined;
			const sessionFile = ctx.sessionManager.getSessionFile();
			if (!sessionFile) return undefined;
			assignmentsOf(event.args).forEach((assignment, itemIndex) => {
				void rate(ctx, sessionFile, event.toolCallId, itemIndex, assignment).catch(error =>
					pi.logger.warn("judge_log: no rating for this dispatch", { sessionFile, toolCallId: event.toolCallId, itemIndex, error: error instanceof Error ? error.message : String(error) }),
				);
			});
			return undefined;
		});
	};
}
