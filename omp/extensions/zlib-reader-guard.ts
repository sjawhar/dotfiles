// Refuse a non-Claude reader of an unmerged zlib pull request in the OSS Verify CI/CD lane.
//
// Harshikaa (the zlib project's owner), 2026-11-26 03:56Z: "add the check in the lane's
// reader-dispatch step that refuses any non-Claude reader on a PR touching projects/zlib/". Her
// rule 8: zlib proof work is Claude-only; a non-Claude model may read only what is already on
// internal main or released, never an unmerged zlib pull request. A program cannot know what a
// reader will read, but it can see the pull requests its task names and the models it will run on;
// this is that check.
//
// Where it acts: in the lane only. That is the omp session LANE_SESSION_ID, or a top-level session
// whose latest `envoy-role-claim` record (pi-envoy's) names LANE_ROLE, and their subagents: a
// session whose transcript sits under the lane's (rootSessionId, the experiments rule; for a
// role-holding root, once this thread saw it hold the role). Every other session's calls are not
// looked at and not logged.
//
// How:
// - A `task` call in the lane: the text each spawn gets (the call's `context` and the item's `task`)
//   is read for pull request numbers (`#3687`, `pull/3687`, `PR 3687`, `pr://3687`, a GitHub URL,
//   `gh pr view 3687`; a reference qualified with another repository is skipped), and kept per item
//   until that spawn starts. Nothing is refused or fetched here.
// - `before_subagent_spawn`, which omp fires for each spawn with the model patterns core resolved
//   for it (task.agentModelOverrides, the agent's frontmatter, the task role, a per-call model), so
//   the guard reads which models a reader runs on from omp itself, not from a list of agent names.
//   A spawn is non-Claude when any pattern, resolved through ctx.models where this machine declares
//   it, names a model whose id holds no "claude". A non-Claude spawn whose text names pull requests
//   has each checked on GitHub with `gh api`. It is refused when one is a pull request of
//   GUARDED_REPO that is not merged into that repository's default branch (open, closed without
//   merging, or merged into another branch) and changes a file under GUARDED_DIR, by its name or the
//   name it was renamed from. It is refused too when that cannot be told: the pull request or its
//   whole files list cannot be read (GitHub lists at most 3,000 files), the check outlives
//   SPAWN_DEADLINE_MS (omp gives this event 30 s, then starts the spawn), or the guard holds no record
//   of the spawn's text. A number that is not a pull request of GUARDED_REPO passes once the
//   repository itself reads, so a 404 from a token that cannot see the repository refuses instead.
// - An eval `agent()` or `workpool()` spawn in the lane: omp fires the same event for it
//   (eval/agent-bridge.ts and task/workpool.ts call runStructuredSubagent, whose applySpawnHook
//   emits it with invocationKind "eval"), but the event carries no text and an eval cell can build
//   the text at run time, so the guard cannot read it: a non-Claude one is refused and a Claude one
//   starts. A non-Claude reader goes through the task tool, where its text is checked.
// - A `write` to `agent://<id>`, a follow-up message, in the lane: when this thread saw <id> start
//   as a non-Claude spawn, the message is checked the same way and refused before it is sent.
//
// Matching a spawn to its item: omp starts a spawn under the agent id it allocated from the item's
// `name` (`Name`, then `Name-2`, …; `Parent.Name` under a subagent), so a named item matches exactly.
// An unnamed spawn may be any waiting unnamed item of its agent type: the guard checks them all and
// refuses while any names a guarded pull request, so an unnamed sibling of a guarded task can be
// refused too (the refusal says so). It never lets the guarded one through, since each spawn uses up
// a waiting item without a guarded reference before one with it. A call's items are dropped when its
// tool result shows no spawn is still to come (no background job).
//
// Left out on purpose: `seance` spawns (omp starts them without this event, and they read past
// transcripts, not pull requests); reads a reader makes later that its task did not name; and
// follow-ups to readers that started before the guard loaded, or in another worker thread. Fallbacks
// in retry.fallbackChains are not read: today every chain keyed by a Claude model lists only Claude.
//
// Each decision in the lane is one JSON line in ~/.omp/zlib-reader-guard.jsonl (pull request
// numbers and verdicts, no task text), written after the decision and not awaited. A failure inside
// the lane refuses with its reason; a failure deciding whether a call is in the lane lets it run, so
// no other session is affected. Registering a `tool_call` handler also keeps omp from launching a
// streamed `task` item before this guard has seen the call (speculation/host.ts,
// hasLifecycleHandlers).
import { execFile } from "node:child_process";
import * as fsp from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { promisify } from "node:util";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { rootSessionId } from "./experiments/gates";

/** The OSS Verify CI/CD lane's omp session. */
export const LANE_SESSION_ID = "01a175dd-5d8a-75ac-99a0-68919637ef66";
/** The lane's Envoy role. */
export const LANE_ROLE = "ossv-cicd";
export const GUARDED_REPO = "theorem-labs/oss-verify-proofs-internal";
export const GUARDED_DIR = "projects/zlib/";

const ROLE_CLAIM_ENTRY = "envoy-role-claim";
const SPAWN_DEADLINE_MS = 20_000;
const MESSAGE_DEADLINE_MS = 60_000;
const GH_TIMEOUT_MS = 15_000;
const VERDICT_TTL_MS = 60_000;
const ROLE_TTL_MS = 60_000;
const MAX_CALLS = 256;
const MAX_STARTED = 1024;
const FILES_PER_PAGE = 100;
/** GitHub lists at most this many files of a pull request. */
const FILES_LISTED_AT_MOST = 3000;
/** The shared GitHub App budget allows at most four calls in parallel (omp/rules/github-api-budget.md). */
const LOOKUPS_AT_ONCE = 4;
const LOG_FILE = path.join(os.homedir(), ".omp", "zlib-reader-guard.jsonl");
const [GUARDED_OWNER, GUARDED_NAME] = GUARDED_REPO.toLowerCase().split("/");

type Model = { id: string; provider: string };
type Entry = { type?: string; customType?: string; data?: unknown; parentId?: string | null };
type Ctx = {
	sessionManager: {
		getSessionId(): string;
		getSessionFile(): string | undefined;
		getLeafEntry?(): Entry | undefined;
		getEntry?(id: string): Entry | undefined;
	};
	models?: { resolve(spec: string): Model | undefined; current?(): Model | undefined };
	agent?: { kind: "main" | "sub" };
};
type ToolCallEvent = { toolName: string; toolCallId: string; input: Record<string, unknown> };
type ToolResultEvent = { toolName: string; toolCallId: string; details?: unknown };
type SpawnEvent = { agent: string; invocationKind: "task" | "eval"; patterns: string[]; spawnKey?: string };
type Scope = { session: string; root: string };
type Refusal = { block: true; reason: string };

export type GhResult = { ok: true; json: unknown } | { ok: false; notFound: boolean; error: string };
/** One `gh api <route>` call. */
export type GhApi = (route: string) => Promise<GhResult>;

export type PrVerdict =
	| { kind: "not-a-pr" }
	| { kind: "on-default-branch"; base: string }
	| { kind: "no-guarded-files"; state: string; files: number }
	| { kind: "guarded"; state: string; merged: boolean; base: string; defaultBranch: string; file: string }
	| { kind: "unreadable"; error: string };

/** A spawn the guard saw start in the lane: whether it runs Claude only, and on what. */
export type Started = { claude: boolean; models: string[] };

export type GuardDeps = {
	gh: GhApi;
	now: () => number;
	/** Writes one log line; the guard does not wait for it. */
	append: (line: string) => void | Promise<void>;
	laneSessionIds: readonly string[];
	spawnDeadlineMs: number;
	messageDeadlineMs: number;
	/** Spawns seen in the lane, by `<root session>\0<agent id>`; shared by this thread's sessions. */
	started: Map<string, Started>;
	/** Top-level sessions seen holding LANE_ROLE; shared by this thread's sessions. */
	roleRoots: Set<string>;
};

/** One spawn a `task` call asked for, kept until it starts. */
type Item = {
	/** Lowercased agent type; undefined when the item named none (the spawn policy's default). */
	agent?: string;
	name?: string;
	/** The item's per-call model, classified; undefined when it named none. */
	perCall?: "claude" | "other";
	refs: number[];
	/** Why the guard cannot read the text this spawn gets, when it cannot. */
	unreadable?: string;
	consumed: boolean;
};

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string | undefined => (typeof value === "string" && value.trim() ? value.trim() : undefined);

const URL_REF = /\bgithub\.com\/(?:repos\/)?([\w.-]+)\/([\w.-]+)\/(?:pulls?|issues)\/(\d{1,7})(?!\d)/gi;
const PR_URL_REF = /\bpr:\/\/(?:([\w.-]+)\/([\w.-]+)\/)?(\d{1,7})(?!\d)/gi;
const HASH_REF = /(?<![\w./&#-])([\w.-]+(?:\/[\w.-]+)?)?#(\d{1,7})(?!\w)/g;
const WORD_REF = /\b(?:PRs?|pull[ -]requests?)\s*(?:no\.?\s*|number\s*)?(\d{1,7})(?!\w)/gi;
const PATH_REF = /(?<![\w./-])pulls?\/(\d{1,7})(?!\w)/gi;
const GH_CLI_REF = /\bgh\s+pr\s+[a-z-]+\s+(\d{1,7})(?!\w)/gi;

const isGuardedRepo = (owner: string | undefined, name: string | undefined) => owner?.toLowerCase() === GUARDED_OWNER && name?.toLowerCase() === GUARDED_NAME;

/** Whether the qualifier before a `#N` leaves it a reference to GUARDED_REPO (or to no repository). */
function hashQualifierIsGuarded(qualifier: string | undefined): boolean {
	if (qualifier === undefined) return true;
	const slash = qualifier.indexOf("/");
	if (slash !== -1) return isGuardedRepo(qualifier.slice(0, slash), qualifier.slice(slash + 1));
	// A plain word (`PR#3687`) is no repository; a name with `-` or `.` is one.
	if (!/[-.]/.test(qualifier)) return true;
	return qualifier.toLowerCase() === GUARDED_NAME;
}

/** The pull request numbers of GUARDED_REPO a text may name, ascending, each once. */
export function pullRequestNumbers(source: string): number[] {
	const found = new Set<number>();
	let rest = source.replace(URL_REF, (_match, owner: string, name: string, n: string) => {
		if (isGuardedRepo(owner, name)) found.add(Number(n));
		return " ";
	});
	rest = rest.replace(PR_URL_REF, (_match, owner: string | undefined, name: string | undefined, n: string) => {
		if (owner === undefined || isGuardedRepo(owner, name)) found.add(Number(n));
		return " ";
	});
	for (const match of rest.matchAll(HASH_REF)) if (hashQualifierIsGuarded(match[1])) found.add(Number(match[2]));
	for (const pattern of [WORD_REF, PATH_REF, GH_CLI_REF]) for (const match of rest.matchAll(pattern)) found.add(Number(match[1]));
	found.delete(0);
	return [...found].sort((a, b) => a - b);
}

const execFileAsync = promisify(execFile);

/** `gh api <route>`, as the session's own GitHub identity; a 404 is told apart from other failures. */
export const ghApi: GhApi = async route => {
	try {
		const { stdout } = await execFileAsync("gh", ["api", route], { timeout: GH_TIMEOUT_MS, killSignal: "SIGKILL", maxBuffer: 64 * 1024 * 1024 });
		return { ok: true, json: JSON.parse(stdout) };
	} catch (error) {
		const stderr = isObject(error) && typeof error.stderr === "string" ? error.stderr : "";
		// The dotfiles gh shim prints its own LEAK CHECK line first; gh's error is the last line.
		const said = stderr
			.split("\n")
			.map(line => line.trim())
			.filter(line => line && !line.startsWith("LEAK CHECK"))
			.at(-1);
		return { ok: false, notFound: /\bHTTP 404\b/.test(stderr), error: said ?? (error instanceof Error ? error.message : String(error)) };
	}
};

/** Whether pull request `n` of GUARDED_REPO is one a non-Claude model may not read, and why. */
export async function checkPullRequest(n: number, gh: GhApi): Promise<PrVerdict> {
	const pr = await gh(`repos/${GUARDED_REPO}/pulls/${n}`);
	if (!pr.ok) {
		if (!pr.notFound) return { kind: "unreadable", error: pr.error };
		const repo = await gh(`repos/${GUARDED_REPO}`);
		if (!repo.ok) return { kind: "unreadable", error: `GitHub answered 404 for it, and ${GUARDED_REPO} itself could not be read (${repo.error})` };
		return { kind: "not-a-pr" };
	}
	const p = pr.json;
	const base = isObject(p) && isObject(p.base) ? p.base : undefined;
	const defaultBranch = base && isObject(base.repo) ? base.repo.default_branch : undefined;
	if (!isObject(p) || typeof p.state !== "string" || typeof p.merged !== "boolean" || typeof p.changed_files !== "number" || typeof base?.ref !== "string" || typeof defaultBranch !== "string") {
		return { kind: "unreadable", error: "GitHub's answer lacks its state, merged flag, changed-file count or base branch" };
	}
	if (p.merged && base.ref === defaultBranch) return { kind: "on-default-branch", base: base.ref };
	let listed = 0;
	for (let page = 1; listed < FILES_LISTED_AT_MOST; page++) {
		const answer = await gh(`repos/${GUARDED_REPO}/pulls/${n}/files?per_page=${FILES_PER_PAGE}&page=${page}`);
		if (!answer.ok) return { kind: "unreadable", error: `page ${page} of its files list: ${answer.error}` };
		if (!Array.isArray(answer.json)) return { kind: "unreadable", error: `page ${page} of its files list is not a list` };
		for (const file of answer.json) {
			const names = isObject(file) ? [file.filename, file.previous_filename].filter((name): name is string => typeof name === "string") : [];
			const hit = names.find(name => name.startsWith(GUARDED_DIR));
			if (hit !== undefined) return { kind: "guarded", state: p.state, merged: p.merged, base: base.ref, defaultBranch, file: hit };
		}
		listed += answer.json.length;
		if (answer.json.length < FILES_PER_PAGE) break;
	}
	if (listed < p.changed_files) return { kind: "unreadable", error: `GitHub listed ${listed} of its ${p.changed_files} changed files` };
	return { kind: "no-guarded-files", state: p.state, files: listed };
}

const refuses = (verdict: PrVerdict) => verdict.kind === "guarded" || verdict.kind === "unreadable";

function describe(n: number, verdict: PrVerdict): string {
	if (verdict.kind === "unreadable") return `#${n}, which it could not check (${verdict.error})`;
	if (verdict.kind !== "guarded") return `#${n}`;
	const where = verdict.merged
		? `merged into ${verdict.base}, not into ${verdict.defaultBranch}`
		: verdict.state === "open"
			? `open, not merged (base ${verdict.base})`
			: `closed without merging (base ${verdict.base})`;
	return `#${n} (${where}; it changes ${verdict.file})`;
}

const isClaudeId = (id: string) => /claude/i.test(id);

/** Whether every model a spawn may run on is Claude, and their names, read through this machine's models. */
export function classifyModels(patterns: readonly string[], ctx: Pick<Ctx, "models">): Started {
	const resolve = (spec: string): Model | undefined => {
		try {
			return ctx.models?.resolve(spec);
		} catch {
			return undefined;
		}
	};
	if (patterns.length === 0) {
		let current: Model | undefined;
		try {
			current = ctx.models?.current?.();
		} catch {
			current = undefined;
		}
		return current ? { claude: isClaudeId(current.id), models: [`${current.provider}/${current.id}`] } : { claude: false, models: ["(no model resolved)"] };
	}
	const models: string[] = [];
	let claude = true;
	for (const pattern of patterns) {
		const model = resolve(pattern);
		models.push(model ? `${model.provider}/${model.id}` : pattern);
		if (!isClaudeId(model ? model.id : pattern)) claude = false;
	}
	return { claude, models };
}

/** Whether the session's latest role claim, for this session, names LANE_ROLE. */
function claimsLaneRole(ctx: Ctx, session: string): boolean {
	const manager = ctx.sessionManager;
	if (!manager.getLeafEntry || !manager.getEntry) return false;
	let entry = manager.getLeafEntry();
	for (let steps = 0; entry && steps < 5_000_000; steps++) {
		if (entry.type === "custom" && entry.customType === ROLE_CLAIM_ENTRY && isObject(entry.data)) {
			const { role, session_id: claimedFor } = entry.data;
			if (typeof claimedFor !== "string" || claimedFor === session) return typeof role === "string" && role.trim() === LANE_ROLE;
		}
		entry = entry.parentId ? manager.getEntry(entry.parentId) : undefined;
	}
	return false;
}

async function withDeadline<T>(work: Promise<T>, ms: number): Promise<T | "expired"> {
	const { promise: expired, resolve: expire } = Promise.withResolvers<"expired">();
	const timer = setTimeout(() => expire("expired"), ms);
	// A check that fails after the deadline won must not become an unhandled rejection in omp's process.
	work.catch(() => {});
	try {
		return await Promise.race([work, expired]);
	} finally {
		clearTimeout(timer);
	}
}

/** Whether the agent id omp allocated for a spawn came from this item name. */
function keyFromName(key: string, name: string): boolean {
	if (key === name || key.endsWith(`.${name}`)) return true;
	const tail = key.slice(key.lastIndexOf(".") + 1);
	return tail.startsWith(`${name}-`) && /^\d+$/.test(tail.slice(name.length + 1));
}

/** Appends to the log: owner-only, like the session files. */
async function appendLog(line: string): Promise<void> {
	await fsp.mkdir(path.dirname(LOG_FILE), { recursive: true, mode: 0o700 });
	await fsp.appendFile(LOG_FILE, line, { mode: 0o600 });
}

const STARTED = new Map<string, Started>();
const ROLE_ROOTS = new Set<string>();

const DEFAULT_DEPS: GuardDeps = {
	gh: ghApi,
	now: Date.now,
	append: appendLog,
	laneSessionIds: [LANE_SESSION_ID],
	spawnDeadlineMs: SPAWN_DEADLINE_MS,
	messageDeadlineMs: MESSAGE_DEADLINE_MS,
	started: STARTED,
	roleRoots: ROLE_ROOTS,
};

const RULE =
	"Harshikaa's rule 8 for zlib: zlib proof work is Claude-only, and a non-Claude model may read only what is already on internal main or released, never an unmerged zlib pull request.";

export function createZlibReaderGuard(overrides: Partial<GuardDeps> = {}): (pi: ExtensionAPI) => void {
	const deps: GuardDeps = { ...DEFAULT_DEPS, ...overrides };
	return pi => {
		/** Items of this session's `task` calls whose spawns have not started, by tool call id. */
		const calls = new Map<string, Item[]>();
		const verdicts = new Map<number, { at: number; verdict: PrVerdict }>();
		let roleCheck: { at: number; session: string; holds: boolean } | undefined;

		const debug = (message: string, error: unknown) => pi.logger.debug(`zlib-reader-guard: ${message}`, { error: error instanceof Error ? error.message : String(error) });
		const record = (entry: Record<string, unknown>) => {
			try {
				void Promise.resolve(deps.append(`${JSON.stringify({ at: new Date(deps.now()).toISOString(), ...entry })}\n`)).catch(error => debug("log write failed", error));
			} catch (error) {
				debug("log write failed", error);
			}
		};

		const laneScope = (ctx: Ctx): Scope | undefined => {
			const session = ctx.sessionManager.getSessionId();
			const root = rootSessionId(ctx.sessionManager.getSessionFile(), session);
			if (deps.laneSessionIds.includes(session) || deps.laneSessionIds.includes(root)) return { session, root };
			if (ctx.agent?.kind === "sub" || root !== session) return deps.roleRoots.has(root) ? { session, root } : undefined;
			const now = deps.now();
			if (!roleCheck || roleCheck.session !== session || now - roleCheck.at > ROLE_TTL_MS) roleCheck = { at: now, session, holds: claimsLaneRole(ctx, session) };
			if (roleCheck.holds) deps.roleRoots.add(session);
			else deps.roleRoots.delete(session);
			return roleCheck.holds ? { session, root } : undefined;
		};

		const verdictOf = async (n: number): Promise<PrVerdict> => {
			const cached = verdicts.get(n);
			if (cached && deps.now() - cached.at < VERDICT_TTL_MS) return cached.verdict;
			const verdict = await checkPullRequest(n, deps.gh);
			if (verdict.kind !== "unreadable") verdicts.set(n, { at: deps.now(), verdict });
			return verdict;
		};
		const checkAll = async (refs: readonly number[]): Promise<Map<number, PrVerdict>> => {
			const found = new Map<number, PrVerdict>();
			let next = 0;
			const lane = async () => {
				while (next < refs.length) {
					const n = refs[next++]!;
					found.set(n, await verdictOf(n));
				}
			};
			await Promise.all(Array.from({ length: Math.min(LOOKUPS_AT_ONCE, refs.length) }, lane));
			return found;
		};

		const remember = (toolCallId: string, items: Item[]) => {
			calls.set(toolCallId, items);
			while (calls.size > MAX_CALLS) calls.delete(calls.keys().next().value as string);
		};
		/** The waiting items a spawn may be: the one its id came from, else every unnamed one of its agent type. */
		const candidates = (agent: string, key: string | undefined, run: "claude" | "other"): Item[] => {
			const waiting = [...calls.values()].flat().filter(item => !item.consumed && (item.agent === undefined || item.agent === agent.toLowerCase()) && (item.perCall === undefined || item.perCall === run));
			const exact = key === undefined ? [] : waiting.filter(item => item.name !== undefined && keyFromName(key, item.name));
			return exact.length > 0 ? exact : waiting.filter(item => item.name === undefined);
		};
		/** Uses up one of the items, one that does not refuse first, and forgets calls with none left. */
		const consume = (items: Item[], refusesItem: (item: Item) => boolean) => {
			const item = items.find(candidate => !refusesItem(candidate)) ?? items[0];
			if (item) item.consumed = true;
			for (const [id, list] of calls) if (list.every(candidate => candidate.consumed)) calls.delete(id);
		};

		const recordTask = (event: ToolCallEvent, ctx: Ctx) => {
			const input = event.input;
			const context = typeof input.context === "string" ? input.context : "";
			const raw = Array.isArray(input.tasks) ? input.tasks : [input];
			const items = raw.filter(isObject).map((item): Item => {
				const model = Array.isArray(item.model) ? item.model.filter((m): m is string => typeof m === "string" && m.trim() !== "") : text(item.model) ? [text(item.model)!] : [];
				return {
					agent: text(item.agent)?.toLowerCase(),
					name: text(item.name),
					perCall: model.length === 0 ? undefined : classifyModels(model, ctx).claude ? "claude" : "other",
					refs: pullRequestNumbers(`${context}\n${typeof item.task === "string" ? item.task : ""}`),
					unreadable: item.taskFile !== undefined ? `its task is in a file (${String(item.taskFile)}) the guard does not read` : undefined,
					consumed: false,
				};
			});
			if (items.length > 0) remember(event.toolCallId, items);
		};

		/** Remembers how a spawn in the lane runs, for the follow-up message check. */
		const noteStarted = (scope: Scope, key: string | undefined, run: Started) => {
			if (key === undefined) return;
			deps.started.set(`${scope.root}\0${key}`, run);
			while (deps.started.size > MAX_STARTED) deps.started.delete(deps.started.keys().next().value as string);
		};
		const spawnSubject = (event: SpawnEvent, run: Started) =>
			`the ${event.agent} subagent${event.spawnKey ? ` ${event.spawnKey}` : ""} was not started. It would run on ${run.models.join(", ")}, which is not Claude only`;
		const instead = "Dispatch a fresh Claude reader instead (for example agent `task` or `reviewer`), or leave the reference out if this reader does not need it.";

		/** An eval `agent()` or `workpool()` spawn: omp gives the guard no text for it, so a non-Claude one is refused. */
		const checkEvalSpawn = (event: SpawnEvent, ctx: Ctx, scope: Scope): Refusal | undefined => {
			const run = classifyModels(event.patterns, ctx);
			noteStarted(scope, event.spawnKey, run);
			const base = { ...scope, event: "eval-spawn", agent: event.agent, spawnKey: event.spawnKey, models: run.models, claude: run.claude };
			if (run.claude) {
				record({ ...base, outcome: "pass", why: "Claude only" });
				return undefined;
			}
			record({ ...base, outcome: "refuse", why: "eval spawn text unreadable" });
			return {
				block: true,
				reason: `zlib reader guard: ${spawnSubject(event, run)}, and it comes from eval \`agent()\` or \`workpool()\`, whose task text omp does not give the guard, so it cannot check which pull requests that text names and refuses it. ${RULE} Dispatch it with the task tool instead, where the guard reads its text, or use a fresh Claude reader (for example agent \`task\` or \`reviewer\`).`,
			};
		};

		const checkSpawn = async (event: SpawnEvent, ctx: Ctx, scope: Scope): Promise<Refusal | undefined> => {
			const run = classifyModels(event.patterns, ctx);
			noteStarted(scope, event.spawnKey, run);
			const items = candidates(event.agent, event.spawnKey, run.claude ? "claude" : "other");
			const base = { ...scope, event: "spawn", agent: event.agent, spawnKey: event.spawnKey, models: run.models, claude: run.claude, matched: items.length };
			const subject = spawnSubject(event, run);
			if (run.claude) {
				consume(items, item => item.refs.length > 0);
				record({ ...base, outcome: "pass", why: "Claude only" });
				return undefined;
			}
			if (items.length === 0) {
				record({ ...base, outcome: "refuse", why: "no record of the task call" });
				return { block: true, reason: `zlib reader guard: ${subject}, and the guard holds no record of the \`task\` call that asked for it, so it cannot read its task text. ${RULE} Dispatch it again with the task tool. ${instead}` };
			}
			const unreadable = items.map(item => item.unreadable).filter((why): why is string => why !== undefined);
			const refs = [...new Set(items.flatMap(item => item.refs))].sort((a, b) => a - b);
			if (refs.length === 0 && unreadable.length === 0) {
				consume(items, () => false);
				record({ ...base, outcome: "pass", why: "names no pull request" });
				return undefined;
			}
			const found = await withDeadline(checkAll(refs), deps.spawnDeadlineMs);
			const ambiguity =
				items.length > 1
					? ` This session has ${items.length} waiting ${event.agent} tasks this spawn may be, and the guard cannot tell which one it is, so it refuses each while any of them names such a pull request; give each task its own \`name\` to have only that one refused.`
					: "";
			if (found === "expired") {
				consume(items, item => item.refs.length > 0 || item.unreadable !== undefined);
				record({ ...base, outcome: "refuse", why: "deadline", refs, deadlineMs: deps.spawnDeadlineMs });
				return { block: true, reason: `zlib reader guard: ${subject}, and the guard could not check the pull requests its task names (${refs.map(n => `#${n}`).join(", ")}) within ${deps.spawnDeadlineMs / 1000} s, so it refuses. ${RULE}${ambiguity} ${instead}` };
			}
			const bad = refs.filter(n => refuses(found.get(n)!));
			const summary = Object.fromEntries(found);
			if (bad.length === 0 && unreadable.length === 0) {
				consume(items, () => false);
				record({ ...base, outcome: "pass", why: "no guarded pull request", refs, verdicts: summary });
				return undefined;
			}
			consume(items, item => item.unreadable !== undefined || item.refs.some(n => bad.includes(n)));
			record({ ...base, outcome: "refuse", why: "guarded pull request", refs, verdicts: summary, unreadable });
			const named = [...bad.map(n => describe(n, found.get(n)!)), ...unreadable].join("; ");
			return {
				block: true,
				reason: `zlib reader guard: ${subject}, and its task text names ${named}. A pull request of ${GUARDED_REPO} that is not merged into its default branch and changes ${GUARDED_DIR} goes to Claude readers only, and so does one the guard cannot check. ${RULE}${ambiguity} ${instead}`,
			};
		};

		const checkMessage = async (event: ToolCallEvent, scope: Scope, target: string): Promise<Refusal | undefined> => {
			const run = deps.started.get(`${scope.root}\0${target}`);
			if (!run || run.claude) return undefined;
			const refs = pullRequestNumbers(typeof event.input.content === "string" ? event.input.content : "");
			if (refs.length === 0) return undefined;
			const base = { ...scope, event: "message", target, models: run.models, refs };
			const subject = `nothing was sent. agent://${target} runs on ${run.models.join(", ")}, which is not Claude only`;
			const instead = "Send it to a fresh Claude reader instead (for example agent `task` or `reviewer`), or leave the reference out.";
			const found = await withDeadline(checkAll(refs), deps.messageDeadlineMs);
			if (found === "expired") {
				record({ ...base, outcome: "refuse", why: "deadline" });
				return { block: true, reason: `zlib reader guard: ${subject}, and the guard could not check the pull requests the message names within ${deps.messageDeadlineMs / 1000} s, so it refuses. ${RULE} ${instead}` };
			}
			const bad = refs.filter(n => refuses(found.get(n)!));
			const summary = Object.fromEntries(found);
			record({ ...base, outcome: bad.length > 0 ? "refuse" : "pass", why: bad.length > 0 ? "guarded pull request" : "no guarded pull request", verdicts: summary });
			if (bad.length === 0) return undefined;
			return { block: true, reason: `zlib reader guard: ${subject}, and the message names ${bad.map(n => describe(n, found.get(n)!)).join("; ")}. ${RULE} ${instead}` };
		};

		const scopeOrNone = (ctx: Ctx): Scope | undefined => {
			try {
				return laneScope(ctx);
			} catch (error) {
				debug("could not tell whether this session is the lane; let through", error);
				return undefined;
			}
		};

		pi.on("tool_call", async (event: ToolCallEvent, ctx: Ctx) => {
			if (event.toolName === "task") {
				const scope = scopeOrNone(ctx);
				if (!scope) return undefined;
				try {
					recordTask(event, ctx);
				} catch (error) {
					// No record: the spawn check refuses this call's non-Claude spawns for want of one.
					debug("could not read a task call", error);
				}
				return undefined;
			}
			if (event.toolName !== "write" || typeof event.input.path !== "string") return undefined;
			const url = event.input.path.trim();
			if (!/^agent:\/\//i.test(url)) return undefined;
			const scope = scopeOrNone(ctx);
			if (!scope) return undefined;
			const target = url.slice("agent://".length).split(/[/?#]/)[0] ?? "";
			try {
				return await checkMessage(event, scope, target);
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				record({ ...scope, event: "message", target, outcome: "refuse", why: "error", error: message });
				return { block: true, reason: `zlib reader guard: nothing was sent to agent://${target}; the guard failed while checking it (${message}), and in the lane it refuses what it cannot check. ${RULE}` };
			}
		});

		pi.on("tool_result", (event: ToolResultEvent) => {
			// Without a background job, every spawn of the call has started (or none will).
			if (event.toolName === "task" && calls.has(event.toolCallId) && !(isObject(event.details) && event.details.async !== undefined)) calls.delete(event.toolCallId);
			return undefined;
		});

		pi.on("before_subagent_spawn", async (event: SpawnEvent, ctx: Ctx) => {
			const scope = scopeOrNone(ctx);
			if (!scope) return undefined;
			try {
				return event.invocationKind === "task" ? await checkSpawn(event, ctx, scope) : checkEvalSpawn(event, ctx, scope);
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error);
				record({ ...scope, event: "spawn", agent: event.agent, spawnKey: event.spawnKey, outcome: "refuse", why: "error", error: message });
				return { block: true, reason: `zlib reader guard: the ${event.agent} subagent was not started; the guard failed while checking it (${message}), and in the lane it refuses what it cannot check. ${RULE}` };
			}
		});
	};
}

export default function zlibReaderGuard(pi: ExtensionAPI): void {
	createZlibReaderGuard()(pi);
}
