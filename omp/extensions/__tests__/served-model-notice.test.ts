import { describe, expect, test } from "bun:test";
import { bindServedModelNotice } from "../served-model-notice-core";

type Pi = Parameters<typeof bindServedModelNotice>[0];
type Model = { provider: string; id: string };
type Sent = { message: { customType: string; content: string; details: Record<string, unknown> }; deliverAs?: string; agent: string };

const OPUS_55: Model = { provider: "anthropic", id: "claude-opus-5-5" };
const OPUS_5: Model = { provider: "anthropic", id: "claude-opus-5" };
const OPUS_48: Model = { provider: "anthropic", id: "claude-opus-4-8" };
const OPUS_47: Model = { provider: "anthropic", id: "claude-opus-4-7" };
const FABLE: Model = { provider: "anthropic", id: "claude-fable-5-1" };
const SONNET: Model = { provider: "anthropic", id: "claude-sonnet-5-5" };
const ROSTER = [OPUS_55, OPUS_5, OPUS_48, OPUS_47, FABLE, SONNET];
const key = (m: Model) => `${m.provider}/${m.id}`;
/** The shape of Sami's config.yml chains, reduced to what placement reads. */
const CHAINS: Record<string, string[]> = {
	"anthropic/claude-opus-5-5": ["anthropic/claude-opus-5", "anthropic/claude-opus-4-8", "anthropic/claude-opus-4-7"],
	"anthropic/claude-opus-5": ["anthropic/claude-opus-4-8", "anthropic/claude-opus-4-7"],
	"anthropic/claude-fable-5-1": ["anthropic/claude-opus-5-5", "anthropic/claude-opus-5", "anthropic/claude-opus-4-8"],
};

/** One process binding, with a model per agent, its branch, and everything the extension sent or recorded. */
function bind(chains: Record<string, string[]> = CHAINS) {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
	const current = new Map<string, Model>();
	const branch = new Map<string, Array<Record<string, unknown>>>();
	const sent: Sent[] = [];
	const entries: Array<{ type: string; data: Record<string, unknown> }> = [];
	let acting = "main";
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => unknown) => handlers.set(event, handler),
		sendMessage: (message: Sent["message"], options?: { deliverAs?: string }) => {
			sent.push({ message, deliverAs: options?.deliverAs, agent: acting });
			const entries = branch.get(acting) ?? [];
			entries.push({ type: "custom_message", id: `e${entries.length}`, customType: message.customType, details: message.details });
			branch.set(acting, entries);
		},
		appendEntry: (type: string, data: Record<string, unknown>) => entries.push({ type, data }),
	} as unknown as Pi;
	const roles: Record<string, string> = { task: "anthropic/claude-opus-5-5", slow: "openai-codex/gpt-5.6-terra:xhigh" };
	bindServedModelNotice(pi, () => chains, role => roles[role]);
	const ctx = (agent: string, idle = false) => ({
		agent: { id: agent, kind: agent === "main" ? "main" : "sub", name: agent },
		isIdle: () => idle,
		models: {
			current: () => current.get(agent),
			// As omp's resolve: an effort suffix is accepted, and a dated id names its model.
			resolve: (spec: string) => ROSTER.find(m => key(m) === spec.replace(/:[a-z]+$/, "").replace(/-\d{8}$/, "")),
		},
		sessionManager: { getBranch: () => branch.get(agent) ?? [] },
	});
	const fire = async (event: string, payload: unknown, agent: string, idle = false) => {
		acting = agent;
		await handlers.get(event)?.(payload, ctx(agent, idle));
	};
	const assistant = (served: Model, extra: Record<string, unknown>) => ({
		role: "assistant",
		provider: served.provider,
		model: served.id,
		stopReason: "toolUse",
		content: [],
		...extra,
	});
	/** Any model switch but a fallback (a person's /model, a restore) appends one plain `model_change`. */
	const switchModel = (agent: string, model: Model) => {
		const before = current.get(agent);
		if (before && key(before) !== key(model)) {
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(model), resolvedModelIsFallback: false });
			branch.set(agent, entries);
		}
		current.set(agent, model);
	};
	return {
		sent,
		entries,
		on: (agent: string, model: Model) => switchModel(agent, model),
		/**
		 * A turn whose request went to `requested` and that `served` answered, with the hooks in omp
		 * 18.13.3's order: `context`, `turn_start`, then the request. `switchTo` is a model change an
		 * earlier extension makes in its own `context` hook (refusal-cooldown's restore): this
		 * extension's hooks see it, while the request still goes to `requested`.
		 */
		turn: async (agent: string, requested: Model, served: Model = requested, extra: Record<string, unknown> & { switchTo?: Model } = {}) => {
			const { switchTo, ...fields } = extra;
			switchModel(agent, switchTo ?? requested);
			await fire("context", {}, agent);
			await fire("turn_start", {}, agent);
			await fire("message_end", { message: assistant(served, fields) }, agent);
		},
		/** omp's chain moved `agent` off `from` (the selector it reports, effort suffix included) onto `to`. */
		fallback: async (agent: string, from: string, to: Model, role = from.replace(/:[a-z]+$/, "")) => {
			current.set(agent, to);
			// omp appends this entry right before it emits the event for the session's own fallback.
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(to), role: "fallback", resolvedModelIsFallback: true });
			branch.set(agent, entries);
			await fire("retry_fallback_applied", { type: "retry_fallback_applied", from, to: key(to), role, reason: "Request failed: 429 rate_limit_error" }, agent);
		},
		/** An event that does not move the session: an advisor's own chain step. */
		advisorFallback: async (agent: string, from: Model, to: Model) =>
			fire("retry_fallback_applied", { type: "retry_fallback_applied", from: key(from), to: key(to), role: "advisor" }, agent),
		compact: async (agent: string, keepFrom: number, idle = true) => {
			const entries = branch.get(agent) ?? [];
			const kept = entries[keepFrom]?.id ?? "past-the-end";
			entries.push({ type: "compaction", id: `c${entries.length}`, firstKeptEntryId: kept });
			await fire("session_compact", {}, agent, idle);
		},
		addMessage: (agent: string) => {
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "message", id: `m${entries.length}` });
			branch.set(agent, entries);
		},
		switchSession: (agent: string) => fire("session_switch", {}, agent),
		navigate: (agent: string) => fire("session_tree", {}, agent),
		/** The same session starting again (resume, revive): its branch, notice included, unchanged. */
		restart: (agent: string) => fire("session_start", {}, agent, true),
		/**
		 * A session omp started on `served`; `fallback` marks it a replacement, `modelRole` is what
		 * session_init records, `roleEntry` adds the second same-model entry omp writes for a spawn role.
		 */
		start: async (agent: string, served: Model, opts: { fallback: boolean; modelRole?: string; roleEntry?: boolean }) => {
			current.set(agent, served);
			branch.set(agent, [
				{ type: "model_change", id: "init", model: key(served), resolvedModelIsFallback: opts.fallback },
				...(opts.roleEntry ? [{ type: "model_change", id: "init-role", model: key(served), role: opts.modelRole, resolvedModelIsFallback: false }] : []),
				{ type: "session_init", id: "si", modelRole: opts.modelRole },
			]);
			await fire("session_start", {}, agent, true);
		},
		/** omp's Fireworks Fast -> Standard degrade: the same event and entry as a chain step. */
		fireworks: async (agent: string, to: Model) => {
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(to), role: "fallback", resolvedModelIsFallback: true });
			branch.set(agent, entries);
			current.set(agent, to);
			await fire("retry_fallback_applied", { type: "retry_fallback_applied", from: key(OPUS_55), to: key(to), role: "fireworks-fast" }, agent);
		},
		/** Drop `agent`'s queued (not yet persisted) notices from the branch, as a hidden nextTurn queue holds them. */
		unpersist: (agent: string) => branch.set(agent, (branch.get(agent) ?? []).filter(e => e.type !== "custom_message")),
	};
}

describe("served-model-notice", () => {
	test("a chain fallback tells the session once, as a steer, naming both models and that it should carry on", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5:xhigh", OPUS_5);
		await s.fallback("main", "anthropic/claude-opus-5-5:xhigh", OPUS_5);
		await s.turn("main", OPUS_5);
		await s.turn("main", OPUS_5);

		expect(s.sent).toHaveLength(1);
		const [notice] = s.sent;
		expect(notice.deliverAs).toBe("steer");
		expect(notice.message.customType).toBe("served-model-notice");
		expect(notice.message.details).toMatchObject({ kind: "chain", asked: "anthropic/claude-opus-5-5:xhigh", served: "anthropic/claude-opus-5", placement: "top" });
		expect(notice.message.content).toContain("asked anthropic/claude-opus-5-5:xhigh, served anthropic/claude-opus-5");
		expect(notice.message.content).toContain("continue the work");
		expect(notice.message.content).toContain("429 rate_limit_error");
		expect(notice.message.content).not.toContain("MODEL BELOW FLOOR");
		// A turn omp moved is not an Anthropic substitution: nothing recorded for it.
		expect(s.entries).toEqual([]);
	});

	test("a walk below the floor names the original model and tells the result to lead with it", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.turn("main", OPUS_5, OPUS_5, { stopReason: "error" });
		await s.fallback("main", "anthropic/claude-opus-5", OPUS_48);
		await s.turn("main", OPUS_48, OPUS_48, { stopReason: "error" });
		await s.fallback("main", "anthropic/claude-opus-4-8", OPUS_47);

		expect(s.sent.map(n => n.message.details.placement)).toEqual(["top", "top", "below"]);
		expect(s.sent.map(n => n.message.details.asked)).toEqual(Array(3).fill("anthropic/claude-opus-5-5"));
		expect(s.sent[2].message.content).toContain("`MODEL BELOW FLOOR: asked anthropic/claude-opus-5-5, served anthropic/claude-opus-4-7`");
	});

	test("a serving model no chain places against the floor is treated as below it", async () => {
		const s = bind({ "anthropic/claude-opus-5-5": ["anthropic/claude-sonnet-5-5"] });
		s.on("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", SONNET);

		expect(s.sent[0].message.details.placement).toBe("unplaced");
		expect(s.sent[0].message.content).toContain("served anthropic/claude-sonnet-5-5 (not placed)`");
	});

	test("a run with no substitution gets nothing", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		s.on("worker", OPUS_55);
		for (const agent of ["main", "worker"]) {
			await s.turn(agent, OPUS_55);
			await s.turn(agent, OPUS_55, OPUS_55, { stopReason: "stop" });
		}
		await s.compact("main", 0);

		expect(s.sent).toEqual([]);
		expect(s.entries).toEqual([]);
	});

	test("an advisor's fallback, which leaves the session's model alone, is not a substitution", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.advisorFallback("main", SONNET, OPUS_5);
		// The corner where the advisor falls back onto the session's own model.
		await s.advisorFallback("main", SONNET, OPUS_55);

		expect(s.sent).toEqual([]);
	});

	test("a subagent gets its own notice and leaves its parent's state alone", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		s.on("worker", OPUS_55);
		await s.turn("worker", OPUS_55);
		await s.fallback("worker", "anthropic/claude-opus-5-5", OPUS_5);
		await s.turn("main", OPUS_55);

		expect(s.sent.map(n => [n.agent, n.message.details.agent])).toEqual([["worker", "sub:worker"]]);
	});

	test("when the asked model serves again, one restored notice, and a later fallback is told afresh", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.turn("main", OPUS_5);
		// refusal-cooldown switches back inside a turn: the request already built completes on the old model.
		await s.turn("main", OPUS_5, OPUS_5, { switchTo: OPUS_55 });
		await s.turn("main", OPUS_55, OPUS_55, { stopReason: "stop" });
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);

		expect(s.sent.map(n => [n.message.details.kind, n.deliverAs])).toEqual([
			["chain", "steer"],
			["restored", "nextTurn"],
			["chain", "steer"],
		]);
		expect(s.sent[1].message.content).toContain("anthropic/claude-opus-5-5, the model you were asked to run, is serving you again");
		// The turn that completed on the old model after the switch is no Anthropic substitution.
		expect(s.entries).toEqual([]);
	});

	test("Anthropic's in-request fallback is recorded every turn and told once, after the turn", async () => {
		const s = bind();
		s.on("main", FABLE);
		const handoff = { type: "fallback", from: { model: "claude-fable-5-1" }, to: { model: "claude-opus-5" } };
		await s.turn("main", FABLE, OPUS_5, { content: [handoff] });
		// Routed turns: no handoff block, the served model still differs from the one asked.
		await s.turn("main", FABLE, OPUS_5);
		await s.turn("main", FABLE, OPUS_5, { stopReason: "stop" });

		expect(s.entries.map(e => [e.type, e.data.asked, e.data.served, e.data.handoff])).toEqual([
			["served-by-other-model", "anthropic/claude-fable-5-1", "anthropic/claude-opus-5", "claude-fable-5-1 -> claude-opus-5"],
			["served-by-other-model", "anthropic/claude-fable-5-1", "anthropic/claude-opus-5", undefined],
			["served-by-other-model", "anthropic/claude-fable-5-1", "anthropic/claude-opus-5", undefined],
		]);
		expect(s.sent).toHaveLength(1);
		expect(s.sent[0].deliverAs).toBe("aside");
		expect(s.sent[0].message.details).toMatchObject({ kind: "anthropic", placement: "top" });
		expect(s.sent[0].message.content).toContain("Anthropic answered a turn of yours with anthropic/claude-opus-5");
	});

	test("an in-request fallback on the turn that ends the run waits for the next prompt rather than forcing a turn", async () => {
		const s = bind();
		s.on("worker", FABLE);
		await s.turn("worker", FABLE, OPUS_5, { stopReason: "stop" });

		expect(s.sent.map(n => n.deliverAs)).toEqual(["nextTurn"]);
	});

	test("refused and failed turns are neither recorded nor told", async () => {
		const s = bind();
		s.on("main", FABLE);
		await s.turn("main", FABLE, OPUS_5, { stopReason: "error", stopDetails: { type: "refusal" } });
		await s.turn("main", FABLE, OPUS_5, { stopReason: "error" });

		expect(s.sent).toEqual([]);
		expect(s.entries).toEqual([]);
	});

	test("a compaction that drops the notice re-sends it once; one that keeps it does not", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		s.addMessage("main");
		// Kept: entry 0 is omp's model_change, entry 1 the notice, and the compaction keeps from it.
		await s.compact("main", 1);
		expect(s.sent).toHaveLength(1);

		// Dropped: the compaction keeps only the later message.
		await s.compact("main", 2);
		expect(s.sent).toHaveLength(2);
		expect(s.sent[1].message.content).toBe(s.sent[0].message.content);
		expect(s.sent[1].message.details).toMatchObject({ kind: "resent-after-compaction", of: s.sent[0].message.details.id });
		expect(s.sent[1].deliverAs).toBe("nextTurn");

		// The re-sent copy is now in the kept window: the next compaction keeps it and sends nothing.
		await s.compact("main", 5);
		expect(s.sent).toHaveLength(2);
	});

	test("a new session in the same process starts with no fallback on record", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.switchSession("main");
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55, OPUS_55, { stopReason: "stop" });

		expect(s.sent.map(n => n.message.details.kind)).toEqual(["chain"]);
	});

	test("a session omp started on a replacement is told at start, naming the role's model when one is on record", async () => {
		const s = bind();
		await s.start("worker", OPUS_5, { fallback: true, modelRole: "task" });
		await s.start("explicit", OPUS_5, { fallback: true });
		await s.start("plain", OPUS_55, { fallback: false, modelRole: "task" });
		// The role's model has no login in this session (the reason omp replaced it): still named.
		await s.start("codex", OPUS_55, { fallback: true, modelRole: "slow" });

		expect(s.sent.map(n => [n.agent, n.deliverAs, n.message.details.kind, n.message.details.asked, n.message.details.served])).toEqual([
			["worker", "nextTurn", "startup", "anthropic/claude-opus-5-5", "anthropic/claude-opus-5"],
			["explicit", "nextTurn", "startup", "the model your parent asked for (omp did not record its name in this session)", "anthropic/claude-opus-5"],
			["codex", "nextTurn", "startup", "openai-codex/gpt-5.6-terra:xhigh", "anthropic/claude-opus-5-5"],
		]);
		expect(s.sent[0].message.content).toContain("omp started this session on anthropic/claude-opus-5 in place of anthropic/claude-opus-5-5");
		// The asked model serving later is a restore, as after a chain fallback.
		await s.turn("worker", OPUS_55, OPUS_55, { stopReason: "stop" });
		expect(s.sent.at(-1)?.message.details.kind).toBe("restored");
	});

	test("a resumed session whose branch already holds its notice is not told again", async () => {
		const s = bind();
		await s.start("worker", OPUS_5, { fallback: true, modelRole: "task" });
		await s.switchSession("worker");
		await s.restart("worker");

		expect(s.sent).toHaveLength(1);
	});

	test("an advisor's fallback onto the model the session just fell back to claims nothing new", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.advisorFallback("main", SONNET, OPUS_5);
		await s.advisorFallback("main", SONNET, OPUS_48);

		expect(s.sent.map(n => n.message.details.served)).toEqual(["anthropic/claude-opus-5"]);
	});

	test("the Fireworks Fast degrade, which keeps the model, is not a substitution", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.fireworks("main", OPUS_5);

		expect(s.sent).toEqual([]);
	});

	test("an in-request fallback on a turn that calls yield waits for the next prompt", async () => {
		const s = bind();
		s.on("worker", FABLE);
		await s.turn("worker", FABLE, OPUS_5, { content: [{ type: "toolCall", name: "yield" }] });

		expect(s.sent.map(n => n.deliverAs)).toEqual(["nextTurn"]);
	});

	test("during a chain fallback an Anthropic handoff names the model the session was first asked to run", async () => {
		const s = bind();
		s.on("main", FABLE);
		await s.turn("main", FABLE);
		await s.fallback("main", "anthropic/claude-fable-5-1", OPUS_55);
		const handoff = { type: "fallback", from: { model: "claude-opus-5-5" }, to: { model: "claude-opus-5" } };
		await s.turn("main", OPUS_55, OPUS_5, { content: [handoff] });

		expect(s.sent.map(n => [n.message.details.kind, n.message.details.asked, n.message.details.served])).toEqual([
			["chain", "anthropic/claude-fable-5-1", "anthropic/claude-opus-5-5"],
			["anthropic", "anthropic/claude-fable-5-1", "anthropic/claude-opus-5"],
		]);
	});

	test("moving in the session tree forgets the fallback, so no restore is told on a branch that never fell back", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.navigate("main");
		await s.turn("main", OPUS_55, OPUS_55, { stopReason: "stop" });

		expect(s.sent.map(n => n.message.details.kind)).toEqual(["chain"]);
	});

	test("a compaction while the latest notice is still queued does not send it twice", async () => {
		const s = bind();
		s.on("main", FABLE);
		await s.turn("main", FABLE, OPUS_5, { stopReason: "stop" });
		s.unpersist("main");
		s.addMessage("main");
		await s.compact("main", 0);

		expect(s.sent).toHaveLength(1);
	});

	test("a restore in flight across a tree move is not taken for an Anthropic substitution", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.turn("main", OPUS_5);
		await s.navigate("main");
		await s.turn("main", OPUS_5, OPUS_5, { switchTo: OPUS_55 });

		expect(s.entries).toEqual([]);
		expect(s.sent.map(n => n.message.details.kind)).toEqual(["chain"]);
	});

	test("a dated id Anthropic reports for the asked model is that model, not a substitution", async () => {
		const s = bind();
		s.on("main", OPUS_5);
		await s.turn("main", OPUS_5, { provider: "anthropic", id: "claude-opus-5-20261001" });

		expect(s.entries).toEqual([]);
		expect(s.sent).toEqual([]);
	});

	test("a top-level session started on a replacement names its default role, or says it has no record", async () => {
		const s = bind();
		await s.start("main", OPUS_5, { fallback: true });

		expect(s.sent.map(n => n.message.details.asked)).toEqual(["the model this session was asked to run (omp did not record its name)"]);
	});

	test("a step back onto the asked model is no substitution; the next turn on it is the restore", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		await s.fallback("main", "anthropic/claude-opus-5", OPUS_55);
		await s.turn("main", OPUS_55, OPUS_55, { stopReason: "stop" });

		expect(s.sent.map(n => n.message.details.kind)).toEqual(["chain", "restored"]);
	});

	test("after a hand /model, a fallback off the new model names it as the one asked", async () => {
		const s = bind();
		s.on("main", OPUS_55);
		await s.turn("main", OPUS_55);
		await s.fallback("main", "anthropic/claude-opus-5-5", OPUS_5);
		s.on("main", OPUS_48);
		await s.turn("main", OPUS_48);
		await s.fallback("main", "anthropic/claude-opus-4-8", OPUS_47);
		await s.turn("main", OPUS_48, OPUS_48, { stopReason: "stop" });

		expect(s.sent.map(n => [n.message.details.kind, n.message.details.asked, n.message.details.served])).toEqual([
			["chain", "anthropic/claude-opus-5-5", "anthropic/claude-opus-5"],
			["chain", "anthropic/claude-opus-4-8", "anthropic/claude-opus-4-7"],
			["restored", "anthropic/claude-opus-4-8", "anthropic/claude-opus-4-7"],
		]);
	});

	test("a startup replacement that falls back and is restored to the replacement keeps the asked model", async () => {
		const s = bind();
		await s.start("worker", OPUS_5, { fallback: true, modelRole: "task", roleEntry: true });
		await s.turn("worker", OPUS_5);
		await s.fallback("worker", "anthropic/claude-opus-5", OPUS_48);
		await s.turn("worker", OPUS_48);
		// refusal-cooldown restores the replacement: the turn in flight still finishes on opus-4-8.
		await s.turn("worker", OPUS_48, OPUS_48, { switchTo: OPUS_5 });
		await s.turn("worker", OPUS_5, OPUS_5, { stopReason: "stop" });

		expect(s.sent.map(n => [n.message.details.kind, n.message.details.asked, n.message.details.served])).toEqual([
			["startup", "anthropic/claude-opus-5-5", "anthropic/claude-opus-5"],
			["chain", "anthropic/claude-opus-5-5", "anthropic/claude-opus-4-8"],
			["moved", "anthropic/claude-opus-5-5", "anthropic/claude-opus-5"],
		]);
		expect(s.entries).toEqual([]);
	});
});
