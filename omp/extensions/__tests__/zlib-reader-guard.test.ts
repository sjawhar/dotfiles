import { describe, expect, test } from "bun:test";
import {
	checkPullRequest,
	classifyModels,
	createZlibReaderGuard,
	type GhApi,
	type GhResult,
	type GuardDeps,
	GUARDED_REPO,
	LANE_ROLE,
	LANE_SESSION_ID,
	pullRequestNumbers,
} from "../zlib-reader-guard";

type Result = { block?: boolean; reason?: string } | undefined;
type Handler = (event: unknown, ctx: unknown) => Promise<Result> | Result;
type Line = Record<string, unknown>;
type Entry = { id: string; parentId: string | null; type: string; customType?: string; data?: unknown };

const LANE_FILE = `/s/-src-oss-verifier/2026-10-26T01-40-04-106Z_${LANE_SESSION_ID}.jsonl`;
const SUB_ID = "01a17a95-ee79-7214-8df2-51394559c2f5";
const SUB_FILE = `/s/-src-oss-verifier/2026-10-26T01-40-04-106Z_${LANE_SESSION_ID}/Helper.jsonl`;
const OTHER_ID = "01a17998-6996-746c-9585-6e829280c54e";
const OTHER_FILE = `/s/-src-y/2026-10-26T19-30-00-000Z_${OTHER_ID}.jsonl`;
const OTHER_SUB_FILE = `/s/-src-y/2026-10-26T19-30-00-000Z_${OTHER_ID}/Helper.jsonl`;
/** The `sol` agent's patterns (omp/agents/sol.md), and the `@review` role's first two. */
const SOL = ["model-gateway-openai/openai/gpt-6.1-sol:xhigh", "model-gateway/openai/gpt-6.1-sol:xhigh"];
const REVIEW = ["model-gateway-openai/openai/gpt-6.1-sol-flex:xhigh", "model-gateway/openai/gpt-6.1-sol-flex:xhigh"];
const CLAUDE = ["model-gateway/claude-opus-5-5:xhigh"];
const DEEP = ["openai-codex/gpt-5.6-terra:xhigh"];

type Pr = { state: "open" | "closed"; merged: boolean; base: string; files: Array<string | { filename: string; previous_filename: string }>; changed?: number };
const ZLIB_FILE = "projects/zlib/fuzz/compress_fuzzer/Proof.v";
const PRS: Record<number, Pr> = {
	3687: { state: "open", merged: false, base: "release/zlib-batch-2026-11-25", files: ["projects/zlib/README.md", ZLIB_FILE] },
	3831: { state: "open", merged: false, base: "main", files: ["dev/stream/ossv-n6-farm", "dev/stream/docs/n6-sizing.md"] },
	100: { state: "closed", merged: true, base: "main", files: [ZLIB_FILE] },
	101: { state: "closed", merged: true, base: "release/zlib-1", files: [ZLIB_FILE] },
	102: { state: "closed", merged: false, base: "main", files: [ZLIB_FILE] },
	103: { state: "open", merged: false, base: "main", files: [{ filename: "projects/zlib-old/Proof.v", previous_filename: ZLIB_FILE }] },
	104: { state: "open", merged: false, base: "main", files: [...Array.from({ length: 150 }, (_, i) => `projects/curl/f${i}.v`), ZLIB_FILE] },
	200: { state: "open", merged: false, base: "main", files: Array.from({ length: 240 }, (_, i) => `projects/curl/f${i}.v`), changed: 250 },
	201: { state: "open", merged: false, base: "main", files: Array.from({ length: 3500 }, (_, i) => `projects/curl/f${i}.v`) },
};

/** A fake `gh api` over PRS: the calls it got, and switches that break it. */
function fakeGitHub() {
	const calls: string[] = [];
	const state = { failAll: false, repoUnreadable: false, hang: false };
	const gh: GhApi = async route => {
		calls.push(route);
		if (state.hang) return new Promise<GhResult>(() => {});
		if (state.failAll) return { ok: false, notFound: false, error: "gh: Bad Gateway (HTTP 502)" };
		if (route === `repos/${GUARDED_REPO}`) return state.repoUnreadable ? { ok: false, notFound: true, error: "gh: Not Found (HTTP 404)" } : { ok: true, json: { full_name: GUARDED_REPO } };
		const match = /^repos\/theorem-labs\/oss-verify-proofs-internal\/pulls\/(\d+)(\/files\?per_page=(\d+)&page=(\d+))?$/.exec(route);
		const pr = match ? PRS[Number(match[1])] : undefined;
		if (!match || !pr) return { ok: false, notFound: true, error: "gh: Not Found (HTTP 404)" };
		if (!match[2]) return { ok: true, json: { state: pr.state, merged: pr.merged, changed_files: pr.changed ?? pr.files.length, base: { ref: pr.base, repo: { default_branch: "main" } } } };
		const per = Number(match[3]);
		const page = Number(match[4]);
		// GitHub stops listing at 3,000 files.
		const listed = pr.files.slice(0, 3000).slice((page - 1) * per, page * per);
		return { ok: true, json: listed.map(file => (typeof file === "string" ? { filename: file } : file)) };
	};
	return { gh, calls, state };
}

const models = {
	resolve: (spec: string) => {
		if (spec.startsWith("@")) return undefined;
		const bare = spec.replace(/:[a-z]+$/, "");
		const slash = bare.indexOf("/");
		return { provider: bare.slice(0, slash), id: bare.slice(slash + 1) };
	},
	current: () => ({ provider: "model-gateway", id: "claude-opus-5-5" }),
};

/** A session: its id, its transcript path (`null`: --no-session), main or sub, and its entries. */
function session(id: string, file: string | null, kind: "main" | "sub" = "main", entries: Entry[] = []) {
	const byId = new Map(entries.map(entry => [entry.id, entry]));
	return {
		cwd: "/work",
		agent: { kind },
		models,
		sessionManager: {
			getSessionId: () => id,
			getSessionFile: () => file ?? undefined,
			getLeafEntry: () => entries.at(-1),
			getEntry: (entryId: string) => byId.get(entryId),
		},
	};
}
const LANE = session(LANE_SESSION_ID, LANE_FILE);

/** Entries in a chain, each the parent of the next. */
function chain(...entries: Array<Omit<Entry, "id" | "parentId">>): Entry[] {
	return entries.map((entry, i) => ({ ...entry, id: `e${i}`, parentId: i === 0 ? null : `e${i - 1}` }));
}
const claim = (role: string | null, sessionId: string) => ({ type: "custom", customType: "envoy-role-claim", data: { role, session_id: sessionId } });
const MESSAGE = { type: "message" };

let n = 0;
function bind(overrides: Partial<GuardDeps> = {}) {
	const github = fakeGitHub();
	const lines: Line[] = [];
	const handlers = new Map<string, Handler>();
	const pi = {
		on: (event: string, handler: Handler) => void handlers.set(event, handler),
		logger: { debug: () => {} },
	};
	createZlibReaderGuard({ gh: github.gh, append: line => void lines.push(JSON.parse(line)), started: new Map(), roleRoots: new Set(), ...overrides })(pi as never);
	const fire = (event: string, payload: Record<string, unknown>, ctx: unknown) => {
		const handler = handlers.get(event);
		if (!handler) throw new Error(`no ${event} handler`);
		return handler({ type: event, ...payload }, ctx);
	};
	/** Any `task` call input; returns its tool call id. */
	const call = (input: Record<string, unknown>, ctx: unknown = LANE) => {
		const toolCallId = `t${++n}`;
		fire("tool_call", { toolName: "task", toolCallId, input }, ctx);
		return toolCallId;
	};
	/** A batch `task` call; returns its tool call id. */
	const task = (items: Array<Record<string, unknown>>, context = "Shared background.", ctx: unknown = LANE) => call({ context, tasks: items }, ctx);
	const spawn = (agent: string, patterns: string[], spawnKey?: string, ctx: unknown = LANE, invocationKind = "task") =>
		fire("before_subagent_spawn", { agent, invocationKind, patterns, spawnKey }, ctx) as Promise<Result>;
	const message = (target: string, content: string, ctx: unknown = LANE) =>
		fire("tool_call", { toolName: "write", toolCallId: `t${++n}`, input: { path: `agent://${target}`, content } }, ctx) as Promise<Result>;
	const result = (toolCallId: string, details: unknown, ctx: unknown = LANE) => fire("tool_result", { toolName: "task", toolCallId, details, isError: false }, ctx);
	return { ...github, lines, call, task, spawn, message, result };
}

describe("pull request numbers in a task's text", () => {
	test("every form that names a pull request of the internal repository is found", () => {
		const text = [
			"Review #3687, PR#12 and PR 13.",
			"theorem-labs/oss-verify-proofs-internal#14 and oss-verify-proofs-internal#15",
			"https://github.com/theorem-labs/oss-verify-proofs-internal/pull/16/files",
			"https://api.github.com/repos/theorem-labs/oss-verify-proofs-internal/pulls/17",
			"https://github.com/theorem-labs/oss-verify-proofs-internal/issues/18",
			"pr://19 and pr://theorem-labs/oss-verify-proofs-internal/20/diff",
			"read pull/21, run `gh pr diff 22`, and pull request 23",
		].join("\n");
		expect(pullRequestNumbers(text)).toEqual([12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 3687]);
	});

	test("a reference qualified with another repository, an anchor or an entity is not one", () => {
		const text = [
			"theorem-labs/oss-verify#40 and oss-verify#41 and sjawhar/oss-verify-proofs-internal#42",
			"https://github.com/sjawhar/oh-my-pi/pull/43 and pr://sjawhar/oh-my-pi/44",
			"file.ts#L45, see notes.md#46, colour #fff, &#47;, issuecomment-48",
		].join("\n");
		expect(pullRequestNumbers(text)).toEqual([]);
	});
});

describe("checking a pull request on GitHub", () => {
	const { gh } = fakeGitHub();
	test("an open zlib pull request is guarded and names the zlib file it changes", async () => {
		expect(await checkPullRequest(3687, gh)).toEqual({ kind: "guarded", state: "open", merged: false, base: "release/zlib-batch-2026-11-25", defaultBranch: "main", file: "projects/zlib/README.md" });
	});
	test("an open pull request without zlib files, read whole, is not", async () => {
		expect(await checkPullRequest(3831, gh)).toEqual({ kind: "no-guarded-files", state: "open", files: 2 });
	});
	test("merged into the default branch: on main, so readable; merged elsewhere or closed unmerged: guarded", async () => {
		expect(await checkPullRequest(100, gh)).toEqual({ kind: "on-default-branch", base: "main" });
		expect(await checkPullRequest(101, gh)).toMatchObject({ kind: "guarded", merged: true, base: "release/zlib-1" });
		expect(await checkPullRequest(102, gh)).toMatchObject({ kind: "guarded", state: "closed", merged: false });
	});
	test("a file renamed out of projects/zlib/ counts; a zlib file on the second page is found", async () => {
		expect(await checkPullRequest(103, gh)).toMatchObject({ kind: "guarded", file: ZLIB_FILE });
		expect(await checkPullRequest(104, gh)).toMatchObject({ kind: "guarded", file: ZLIB_FILE });
	});
	test("a files list GitHub does not give whole is unreadable", async () => {
		expect(await checkPullRequest(200, gh)).toEqual({ kind: "unreadable", error: "GitHub listed 240 of its 250 changed files" });
		expect(await checkPullRequest(201, gh)).toEqual({ kind: "unreadable", error: "GitHub listed 3000 of its 3500 changed files" });
	});
	test("a 404 is not a pull request only while the repository itself reads", async () => {
		const github = fakeGitHub();
		expect(await checkPullRequest(4000, github.gh)).toEqual({ kind: "not-a-pr" });
		github.state.repoUnreadable = true;
		expect(await checkPullRequest(4000, github.gh)).toMatchObject({ kind: "unreadable" });
		github.state.failAll = true;
		expect(await checkPullRequest(3831, github.gh)).toEqual({ kind: "unreadable", error: "gh: Bad Gateway (HTTP 502)" });
	});
});

describe("which models a spawn runs on", () => {
	test("Claude on any route is Claude; GPT, Gemini, an unresolved role or no model is not", () => {
		expect(classifyModels(CLAUDE, { models })).toEqual({ claude: true, models: ["model-gateway/claude-opus-5-5"] });
		expect(classifyModels(["anthropic/claude-opus-5-5:xhigh", "model-gateway/anthropic/claude-fable-5-1"], { models }).claude).toBe(true);
		expect(classifyModels(SOL, { models }).claude).toBe(false);
		expect(classifyModels(["model-gateway/claude-opus-5-5", ...DEEP], { models }).claude).toBe(false);
		expect(classifyModels(["google/gemini-3.7-flash:medium"], { models }).claude).toBe(false);
		expect(classifyModels(["@review"], { models })).toEqual({ claude: false, models: ["@review"] });
		expect(classifyModels([], { models: { resolve: () => undefined } }).claude).toBe(false);
		expect(classifyModels([], { models }).claude).toBe(true);
	});
});

describe("a dispatch in the lane", () => {
	test("a sol reader naming open zlib PR #3687 is refused, and told to use a fresh Claude reader", async () => {
		const g = bind();
		g.task([{ name: "Rev3687Sol", agent: "sol", task: "Review pull request #3687." }]);
		const result = await g.spawn("sol", SOL, "Rev3687Sol");
		expect(result?.block).toBe(true);
		expect(result?.reason).toContain("#3687 (open, not merged (base release/zlib-batch-2026-11-25); it changes projects/zlib/README.md)");
		expect(result?.reason).toContain("fresh Claude reader");
		expect(result?.reason).toContain("model-gateway-openai/openai/gpt-6.1-sol");
		expect(g.lines).toHaveLength(1);
		expect(g.lines[0]).toMatchObject({ outcome: "refuse", why: "guarded pull request", refs: [3687], agent: "sol", spawnKey: "Rev3687Sol", session: LANE_SESSION_ID });
	});

	test("a sol reader naming non-zlib PR #3831 starts", async () => {
		const g = bind();
		g.task([{ name: "Rev3831Sol", agent: "sol", task: "Review https://github.com/theorem-labs/oss-verify-proofs-internal/pull/3831" }]);
		expect(await g.spawn("sol", SOL, "Rev3831Sol")).toBeUndefined();
		expect(g.lines[0]).toMatchObject({ outcome: "pass", why: "no guarded pull request", refs: [3831] });
	});

	test("a Claude reader naming #3687 starts, and the guard asks GitHub nothing", async () => {
		const g = bind();
		g.task([{ name: "Rev3687Claude", agent: "task", task: "Review pull request #3687." }]);
		expect(await g.spawn("task", CLAUDE, "Rev3687Claude")).toBeUndefined();
		expect(g.calls).toEqual([]);
		expect(g.lines[0]).toMatchObject({ outcome: "pass", why: "Claude only" });
	});

	test("every non-Claude agent type is refused alike, the @review agents and deep included", async () => {
		for (const [agent, patterns] of [["bug-finder", REVIEW], ["code-reviewer", REVIEW], ["deep", DEEP], ["ux-designer", ["google/gemini-3.7-flash:medium"]]] as const) {
			const g = bind();
			g.task([{ name: `R-${agent}`, agent, task: "Check #3687." }]);
			expect((await g.spawn(agent, [...patterns], `R-${agent}`))?.block).toBe(true);
		}
	});

	test("the PR named only in the shared context still reaches the reader, so it is refused", async () => {
		const g = bind();
		g.task([{ name: "A", agent: "sol", task: "Summarise the CI failures." }], "Background: zlib work is in #3687.");
		expect((await g.spawn("sol", SOL, "A"))?.block).toBe(true);
	});

	test("a sol reader naming no pull request starts without a GitHub call", async () => {
		const g = bind();
		g.task([{ name: "Plain", agent: "sol", task: "Explain the farm's sizing rule." }]);
		expect(await g.spawn("sol", SOL, "Plain")).toBeUndefined();
		expect(g.calls).toEqual([]);
	});

	test("merged into main reads; merged into a release branch, closed unmerged, or renamed out does not", async () => {
		const g = bind();
		g.task([
			{ name: "M", agent: "sol", task: "#100" },
			{ name: "R", agent: "sol", task: "#101" },
			{ name: "C", agent: "sol", task: "#102" },
			{ name: "N", agent: "sol", task: "#103" },
		]);
		expect(await g.spawn("sol", SOL, "M")).toBeUndefined();
		expect((await g.spawn("sol", SOL, "R"))?.reason).toContain("merged into release/zlib-1, not into main");
		expect((await g.spawn("sol", SOL, "C"))?.reason).toContain("closed without merging");
		expect((await g.spawn("sol", SOL, "N"))?.block).toBe(true);
	});

	test("named items match their own spawn, also as Name-2 and Parent.Name", async () => {
		const g = bind();
		g.task([
			{ name: "Zl", agent: "sol", task: "#3687" },
			{ name: "Ok", agent: "sol", task: "#3831" },
		]);
		expect(await g.spawn("sol", SOL, "Ok-2")).toBeUndefined();
		expect((await g.spawn("sol", SOL, "Lane.Zl"))?.block).toBe(true);
	});

	test("a flat task call is read too", async () => {
		const g = bind();
		g.call({ agent: "sol", name: "Flat", task: "pr://3687" });
		expect((await g.spawn("sol", SOL, "Flat"))?.block).toBe(true);
	});

	test("unnamed tasks of one agent type: each is refused while one names a zlib PR, and the zlib one never starts", async () => {
		for (const order of [0, 1]) {
			const g = bind();
			const items = [
				{ agent: "sol", task: "Review #3687." },
				{ agent: "sol", task: "Review #3831." },
			];
			g.task(order === 0 ? items : items.reverse());
			const first = await g.spawn("sol", SOL, "Gen1");
			const second = await g.spawn("sol", SOL, "Gen2");
			expect([first?.block, second?.block]).toEqual([true, true]);
			expect(first?.reason).toContain("give each task its own `name`");
		}
	});

	test("an unnamed task naming no PR beside an unnamed zlib one cannot carry the zlib one through", async () => {
		const g = bind();
		g.task([
			{ agent: "sol", task: "Summarise the logs." },
			{ agent: "sol", task: "Review #3687." },
		]);
		expect((await g.spawn("sol", SOL, "Gen1"))?.block).toBe(true);
		expect((await g.spawn("sol", SOL, "Gen2"))?.block).toBe(true);
	});

	test("an unnamed sol task naming only #3831 starts", async () => {
		const g = bind();
		g.task([{ agent: "sol", task: "Review #3831." }]);
		expect(await g.spawn("sol", SOL, "SwiftOtter")).toBeUndefined();
	});

	test("a non-Claude spawn the guard holds no record of is refused; a Claude one starts", async () => {
		const g = bind();
		expect((await g.spawn("sol", SOL, "Ghost"))?.reason).toContain("holds no record of the `task` call");
		expect(await g.spawn("scout", CLAUDE, "Ghost2")).toBeUndefined();
	});

	test("a call whose tool result shows no background job is dropped: its stale items refuse nothing later", async () => {
		const g = bind();
		const failed = g.task([{ agent: "sol", task: "Review #3687." }]);
		g.result(failed, { results: [] });
		g.task([{ agent: "sol", task: "Review #3831." }]);
		expect(await g.spawn("sol", SOL, "SwiftOtter")).toBeUndefined();
	});

	test("a call with a background job keeps its items for the spawns still to come", async () => {
		const g = bind();
		const id = g.task([{ name: "Later", agent: "sol", task: "Review #3687." }]);
		g.result(id, { results: [], async: { state: "running", jobId: "Later", type: "task" } });
		expect((await g.spawn("sol", SOL, "Later"))?.block).toBe(true);
	});

	test("a per-call model is matched to the spawn that runs it", async () => {
		const g = bind();
		g.task([
			{ agent: "task", model: "model-gateway-openai/openai/gpt-6.1-sol", task: "Review #3687." },
			{ agent: "task", task: "Review #3831." },
		]);
		expect(await g.spawn("task", CLAUDE, "Gen1")).toBeUndefined();
		expect((await g.spawn("task", ["model-gateway-openai/openai/gpt-6.1-sol"], "Gen2"))?.block).toBe(true);
	});

	test("a task in a file the guard does not read is refused for a non-Claude reader", async () => {
		const g = bind();
		g.task([{ name: "F", agent: "sol", taskFile: "local://brief.md" }]);
		expect((await g.spawn("sol", SOL, "F"))?.reason).toContain("does not read");
	});
});

describe("what the guard cannot check, it refuses in the lane", () => {
	test("GitHub failing refuses the sol reader (the control that the check can fail)", async () => {
		const g = bind();
		g.state.failAll = true;
		g.task([{ name: "A", agent: "sol", task: "Review #3831." }]);
		const result = await g.spawn("sol", SOL, "A");
		expect(result?.reason).toContain("#3831, which it could not check (gh: Bad Gateway (HTTP 502))");
	});

	test("a files list GitHub gives only in part refuses", async () => {
		const g = bind();
		g.task([{ name: "A", agent: "sol", task: "Review #200." }]);
		expect((await g.spawn("sol", SOL, "A"))?.reason).toContain("GitHub listed 240 of its 250 changed files");
	});

	test("a GitHub check past its deadline refuses", async () => {
		const g = bind({ spawnDeadlineMs: 50 });
		g.state.hang = true;
		g.task([{ name: "A", agent: "sol", task: "Review #3831." }]);
		expect((await g.spawn("sol", SOL, "A"))?.reason).toContain("within 0.05 s");
	});

	test("a number that is no PR passes, unless the repository cannot be read", async () => {
		const g = bind();
		g.task([{ name: "A", agent: "sol", task: "See #4000." }]);
		expect(await g.spawn("sol", SOL, "A")).toBeUndefined();
		const blind = bind();
		blind.state.repoUnreadable = true;
		blind.task([{ name: "A", agent: "sol", task: "See #4000." }]);
		expect((await blind.spawn("sol", SOL, "A"))?.block).toBe(true);
	});
});

describe("where the guard acts", () => {
	test("another session's sol reader on #3687 starts untouched: no GitHub call, no log line", async () => {
		const g = bind();
		for (const ctx of [session(OTHER_ID, OTHER_FILE), session(OTHER_ID, null), session(SUB_ID, OTHER_SUB_FILE, "sub")]) {
			g.task([{ name: "A", agent: "sol", task: "Review #3687." }], "x", ctx);
			expect(await g.spawn("sol", SOL, "A", ctx)).toBeUndefined();
			expect(await g.spawn("sol", SOL, "Ghost", ctx)).toBeUndefined();
		}
		expect(g.calls).toEqual([]);
		expect(g.lines).toEqual([]);
	});

	test("a subagent of the lane is the lane", async () => {
		const g = bind();
		const sub = session(SUB_ID, SUB_FILE, "sub");
		g.task([{ name: "A", agent: "sol", task: "Review #3687." }], "x", sub);
		expect((await g.spawn("sol", SOL, "Helper.A", sub))?.block).toBe(true);
	});

	test("a session whose latest role claim is ossv-cicd is the lane, and so are its subagents in this thread", async () => {
		const roleRoots = new Set<string>();
		const g = bind({ roleRoots });
		const holder = session(OTHER_ID, OTHER_FILE, "main", chain(MESSAGE, claim("someone-else", OTHER_ID), MESSAGE, claim(LANE_ROLE, OTHER_ID), MESSAGE));
		g.task([{ name: "A", agent: "sol", task: "Review #3687." }], "x", holder);
		expect((await g.spawn("sol", SOL, "A", holder))?.block).toBe(true);
		const sub = session(SUB_ID, OTHER_SUB_FILE, "sub");
		g.task([{ name: "B", agent: "sol", task: "Review #3687." }], "x", sub);
		expect((await g.spawn("sol", SOL, "Helper.B", sub))?.block).toBe(true);
	});

	test("a role released, moved on, or claimed for another session is not the lane", async () => {
		for (const entries of [
			chain(claim(LANE_ROLE, OTHER_ID), MESSAGE, claim(null, OTHER_ID)),
			chain(claim(LANE_ROLE, OTHER_ID), claim("linux-baseline", OTHER_ID)),
			chain(claim(LANE_ROLE, SUB_ID)),
		]) {
			const g = bind();
			const ctx = session(OTHER_ID, OTHER_FILE, "main", entries);
			g.task([{ name: "A", agent: "sol", task: "Review #3687." }], "x", ctx);
			expect(await g.spawn("sol", SOL, "A", ctx)).toBeUndefined();
		}
	});

	test("eval agent() spawns are not looked at", async () => {
		const g = bind();
		expect(await g.spawn("sol", SOL, "EvalAgent", LANE, "eval")).toBeUndefined();
		expect(g.lines).toEqual([]);
	});
});

describe("a follow-up message to a reader", () => {
	test("naming #3687 to a sol reader the lane started is refused; to a Claude reader or an unknown agent it is sent", async () => {
		const g = bind();
		g.task([
			{ name: "SolR", agent: "sol", task: "Review #3831." },
			{ name: "ClaudeR", agent: "task", task: "Review #3831." },
		]);
		expect(await g.spawn("sol", SOL, "SolR")).toBeUndefined();
		expect(await g.spawn("task", CLAUDE, "ClaudeR")).toBeUndefined();
		const refused = await g.message("SolR", "Round 2: also read #3687.");
		expect(refused?.reason).toContain("nothing was sent. agent://SolR runs on");
		expect(refused?.reason).toContain("fresh Claude reader");
		expect(await g.message("SolR", "Round 2: #3831 again.")).toBeUndefined();
		expect(await g.message("ClaudeR", "Also read #3687.")).toBeUndefined();
		expect(await g.message("Stranger", "Also read #3687.")).toBeUndefined();
	});

	test("a lane subagent's message to the sol reader its root started is checked too", async () => {
		const started = new Map();
		const g = bind({ started });
		g.task([{ name: "SolR", agent: "sol", task: "Review #3831." }]);
		await g.spawn("sol", SOL, "SolR");
		expect((await g.message("SolR", "Look at #3687.", session(SUB_ID, SUB_FILE, "sub")))?.block).toBe(true);
	});
});
