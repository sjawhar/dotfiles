import { afterEach, describe, expect, setSystemTime, test } from "bun:test";
import extension from "../refusal-cooldown";

type Pi = Parameters<typeof extension>[0];
type Model = { provider: string; id: string };

const HOUR = 60 * 60 * 1000;
const T0 = new Date("2026-10-11T12:00:00Z");
const OPUS_55: Model = { provider: "anthropic", id: "claude-opus-5-5" };
const OPUS_5: Model = { provider: "anthropic", id: "claude-opus-5" };
const OPUS_48: Model = { provider: "anthropic", id: "claude-opus-4-8" };
const key = (m: Model) => `${m.provider}/${m.id}`;

type Health = { state: "healthy" | "reserve" | "depleted" | "unknown"; accounts: Array<{ resetsAt?: number }> };

/** One process's binding of the extension, with a model per agent and the models it was set to. */
function bind(roster: Model[] = [OPUS_55, OPUS_5, OPUS_48]) {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<unknown>>();
	const current = new Map<string, Model>();
	const set: Array<[string, string]> = [];
	/** Each thinking level the extension set, with the agent it set it for. */
	const levels: Array<[string, string]> = [];
	/** Usage health the auth store reports per `provider/id`; absent means healthy. */
	const health = new Map<string, Health | Error>();
	const healthQueries: string[] = [];
	let acting = "";
	/** Each agent's branch: the `model_change` entries omp appends on every model switch. */
	const branch = new Map<string, Array<{ type: string; id: string; model?: string; role?: string; resolvedModelIsFallback?: boolean; configured?: string }>>();
	const level = new Map<string, string>();
	/** A model switch other than a fallback (a person's /model, omp's own restore): one plain entry. */
	const switchTo = (agent: string, model: Model) => {
		const entries = branch.get(agent) ?? [];
		if (current.has(agent)) entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(model), resolvedModelIsFallback: false });
		branch.set(agent, entries);
		current.set(agent, model);
	};
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => Promise<unknown>) => handlers.set(event, handler),
		setModel: async (model: Model) => {
			set.push([acting, key(model)]);
			switchTo(acting, model);
			return true;
		},
		getThinkingLevel: () => level.get(acting),
		setThinkingLevel: (to: string) => {
			levels.push([acting, to]);
			level.set(acting, to);
		},
	} as unknown as Pi;
	extension(pi);
	const ctx = (agent: string) => ({
		agent: { id: agent },
		sessionManager: { getSessionId: () => `session-${agent}`, getBranch: () => branch.get(agent) ?? [] },
		modelRegistry: {
			authStorage: {
				health: {
					model: async (provider: string, options: { modelId?: string }) => {
						const selector = `${provider}/${options.modelId}`;
						healthQueries.push(selector);
						const reported = health.get(selector) ?? { state: "healthy", accounts: [] };
						if (reported instanceof Error) throw reported;
						return reported;
					},
				},
			},
		},
		models: {
			current: () => current.get(agent),
			// omp's resolve accepts an effort suffix, as `retry_fallback_applied`'s `from` carries one.
			resolve: (selector: string) => roster.find(m => key(m) === selector.replace(/:[a-z]+$/, "")),
		},
	});
	return {
		set,
		levels,
		health,
		healthQueries,
		model: (agent: string) => key(current.get(agent) as Model),
		on: (agent: string, model: Model) => switchTo(agent, model),
		/** A person's /effort. */
		effort: (agent: string, to: string) => level.set(agent, to),
		/** Another session resumed in this process, under the same agent id, on `model`. */
		resume: (agent: string, model: Model) => {
			branch.set(agent, [{ type: "model_change", id: "resumed0", model: key(model), resolvedModelIsFallback: false }]);
			current.set(agent, model);
		},
		/** omp started `agent` on `model` in place of its configured model (the flagged entry, then the role entry). */
		startedOnReplacement: (agent: string, model: Model) => {
			branch.set(agent, [
				{ type: "model_change", id: "init", model: key(model), resolvedModelIsFallback: true },
				{ type: "model_change", id: "init-role", model: key(model), role: "task", resolvedModelIsFallback: false },
			]);
			current.set(agent, model);
		},
		/** The effort recorded at creation (`auto` or a level), as omp writes it before any fallback. */
		configuredEffort: (agent: string, configured: string) => {
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "thinking_level_change", id: `tl${entries.length}`, configured });
			branch.set(agent, entries);
		},
		/** omp's Fireworks Fast degrade: the same event and entry as a chain step, role `fireworks-fast`. */
		fireworks: async (agent: string, from: Model, to: Model) => {
			current.set(agent, to);
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(to), role: "fallback", resolvedModelIsFallback: true });
			branch.set(agent, entries);
			acting = agent;
			await handlers.get("retry_fallback_applied")?.({ from: key(from), to: key(to), role: "fireworks-fast" }, ctx(agent));
		},
		/** The chain moved `agent` off `from` onto `to`; `fromSelector` is `from` as omp names it, effort included. */
		fallback: async (agent: string, from: Model, to: Model, fromSelector = key(from), fallbackLevel?: string) => {
			current.set(agent, to);
			if (fallbackLevel) level.set(agent, fallbackLevel);
			const entries = branch.get(agent) ?? [];
			entries.push({ type: "model_change", id: `mc${entries.length}`, model: key(to), role: "fallback", resolvedModelIsFallback: true });
			branch.set(agent, entries);
			acting = agent;
			await handlers.get("retry_fallback_applied")?.({ from: fromSelector, to: key(to) }, ctx(agent));
		},
		/** An advisor's own model failed over: same event, no session model change, no entry. */
		advisorFallback: async (agent: string, from: string, to: Model) =>
			handlers.get("retry_fallback_applied")?.({ from, to: key(to), role: "advisor" }, ctx(agent)),
		/** The next provider request for `agent`. */
		request: async (agent: string) => {
			acting = agent;
			await handlers.get("context")?.({}, ctx(agent));
		},
	};
}

afterEach(() => setSystemTime());

describe("refusal-cooldown", () => {
	test("a session moved down the chain stays there until the cooldown passes", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);

		setSystemTime(new Date(T0.getTime() + HOUR - 1));
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_5));
		expect(s.set).toEqual([]);
	});

	test("after the cooldown, the next request inside the same tool loop restores the configured model, once", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_55));
		expect(s.set).toEqual([["main", key(OPUS_55)]]);
	});

	test("a model chosen by hand after the restore is kept", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		s.on("main", OPUS_48);
		setSystemTime(new Date(T0.getTime() + 3 * HOUR));
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_48));
		expect(s.set).toEqual([["main", key(OPUS_55)]]);
	});

	test("a chain that walks several models restores the model the session started on", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		await s.fallback("main", OPUS_5, OPUS_48);

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_55));
	});

	test("a session omp already put back is left alone, though the event named the model with its effort", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:xhigh");
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:xhigh");
		s.on("main", OPUS_55);

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");
		expect(s.set).toEqual([]);

		// Disarmed: a later move off the model, by hand, is not reverted either.
		s.on("main", OPUS_48);
		setSystemTime(new Date(T0.getTime() + 3 * HOUR));
		await s.request("main");
		expect(s.set).toEqual([]);
	});

	test("a fallback named with its effort restores that model at that effort once the cooldown passes", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		s.configuredEffort("main", "xhigh");
		// The chain entry carried its own effort (`…opus-5:low`); setModel alone would keep it.
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:xhigh", "low");

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_55)]]);
		expect(s.levels).toEqual([["main", "xhigh"]]);
	});

	test("a model chosen by hand during the cooldown is kept, and a fallback off it restores to it", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR / 3));
		s.on("main", OPUS_48);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");
		expect(s.set).toEqual([]);

		// The hand-chosen model falls back later: the restore goes to it, not to the first model.
		await s.fallback("main", OPUS_48, OPUS_5);
		setSystemTime(new Date(T0.getTime() + 2 * HOUR));
		await s.request("main");
		expect(s.set).toEqual([["main", key(OPUS_48)]]);
	});

	test("an effort chosen by hand during the cooldown is kept", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:xhigh", "low");
		s.effort("main", "medium");
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_55)]]);
		expect(s.levels).toEqual([]);
	});

	test("a second fallback after omp's own restore waits a full cooldown", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR / 12));
		s.on("main", OPUS_55);
		setSystemTime(new Date(T0.getTime() + (HOUR * 5) / 6));
		await s.fallback("main", OPUS_55, OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR + 1));
		await s.request("main");
		expect(s.set).toEqual([]);

		setSystemTime(new Date(T0.getTime() + (HOUR * 11) / 6));
		await s.request("main");
		expect(s.set).toEqual([["main", key(OPUS_55)]]);
	});

	test("after omp's own restore, a hand /model onto the fallback's model is kept", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		s.on("main", OPUS_55);
		s.on("main", OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([]);
	});

	test("another session resumed in this process on the fallback's model is never restored", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		s.resume("main", OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([]);
	});

	test("the Fireworks Fast degrade is not armed", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fireworks("main", OPUS_55, OPUS_5);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([]);
	});

	test("a session configured for auto effort is restored without pinning one", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		s.configuredEffort("main", "auto");
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:high", "high");
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_55)]]);
		expect(s.levels).toEqual([]);
	});

	test("a session with no effort on record (auto, never classified) is restored without pinning one", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5, "anthropic/claude-opus-5-5:high", "high");
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_55)]]);
		expect(s.levels).toEqual([]);
	});

	test("a refusal off a model omp started the session on is restored to that model, not left lower", async () => {
		setSystemTime(T0);
		const s = bind();
		// omp started the session on opus-5 in place of its configured model; its own state toward that
		// model is pinned (usage preflight) or pinned by this refusal, so omp never restores.
		s.startedOnReplacement("main", OPUS_5);
		await s.fallback("main", OPUS_5, OPUS_48);
		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_5)]]);
	});

	test("one agent's fallback neither restores nor delays another agent", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_55);
		s.on("sub", OPUS_55);
		await s.fallback("sub", OPUS_55, OPUS_5);

		setSystemTime(new Date(T0.getTime() + HOUR));
		s.on("main", OPUS_48);
		await s.request("main");
		await s.request("sub");

		expect(s.model("main")).toBe(key(OPUS_48));
		expect(s.model("sub")).toBe(key(OPUS_55));
		expect(s.set).toEqual([["sub", key(OPUS_55)]]);
	});

	test("an advisor's fallback never moves the session onto the advisor's model, nor blocks a later arming", async () => {
		setSystemTime(T0);
		const SONNET: Model = { provider: "anthropic", id: "claude-sonnet-5-5" };
		const s = bind([OPUS_55, OPUS_5, OPUS_48, SONNET]);
		s.on("main", OPUS_5);
		// The advisor (Sonnet 5.5) falls onto the session's own model: same event shape, no entry.
		await s.advisorFallback("main", "anthropic/claude-sonnet-5-5:medium", OPUS_5);
		// A real session fallback after it, onto the model the advisor just named.
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);
		// The advisor again, onto that same model: the session's entry is already claimed.
		await s.advisorFallback("main", "anthropic/claude-sonnet-5-5:medium", OPUS_5);

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.set).toEqual([["main", key(OPUS_55)]]);
	});

	test("a model chosen by hand, with no fallback, is never changed", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("main", OPUS_48);

		setSystemTime(new Date(T0.getTime() + 2 * HOUR));
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_48));
		expect(s.set).toEqual([]);
	});

	test("a configured model that has left the roster is not restored", async () => {
		setSystemTime(T0);
		const s = bind([OPUS_5, OPUS_48]);
		s.on("main", OPUS_55);
		await s.fallback("main", OPUS_55, OPUS_5);

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("main");

		expect(s.model("main")).toBe(key(OPUS_5));
		expect(s.set).toEqual([]);
	});

	test("a configured model whose usage window is exhausted stays off until the reported reset", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("sub", OPUS_55);
		await s.fallback("sub", OPUS_55, OPUS_5);
		const reset = T0.getTime() + 5 * 24 * HOUR;
		s.health.set(key(OPUS_55), { state: "depleted", accounts: [{ resetsAt: reset }] });

		// Every few hours until the reset: no restore, and the store is asked only once.
		for (let hour = 1; hour < 5 * 24; hour += 7) {
			setSystemTime(new Date(T0.getTime() + hour * HOUR));
			await s.request("sub");
		}
		expect(s.model("sub")).toBe(key(OPUS_5));
		expect(s.set).toEqual([]);
		expect(s.healthQueries).toEqual([key(OPUS_55)]);

		// At the reset the store reports the window open again, and the model comes back once.
		s.health.set(key(OPUS_55), { state: "healthy", accounts: [] });
		setSystemTime(new Date(reset));
		await s.request("sub");
		await s.request("sub");
		expect(s.model("sub")).toBe(key(OPUS_55));
		expect(s.set).toEqual([["sub", key(OPUS_55)]]);
	});

	test("a model inside its reserve margin is not restored either, and is checked again only after another cooldown", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("sub", OPUS_55);
		await s.fallback("sub", OPUS_55, OPUS_5);
		s.health.set(key(OPUS_55), { state: "reserve", accounts: [] });

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("sub");
		expect(s.set).toEqual([]);

		setSystemTime(new Date(T0.getTime() + HOUR + 10 * 60 * 1000));
		await s.request("sub");
		expect(s.healthQueries).toEqual([key(OPUS_55)]);

		s.health.set(key(OPUS_55), { state: "healthy", accounts: [] });
		setSystemTime(new Date(T0.getTime() + 2 * HOUR));
		await s.request("sub");
		expect(s.model("sub")).toBe(key(OPUS_55));
	});

	test("unknown or unreadable usage restores as before", async () => {
		setSystemTime(T0);
		const s = bind();
		s.on("a", OPUS_55);
		s.on("b", OPUS_55);
		await s.fallback("a", OPUS_55, OPUS_5);
		await s.fallback("b", OPUS_55, OPUS_5);
		s.health.set(key(OPUS_55), { state: "unknown", accounts: [] });

		setSystemTime(new Date(T0.getTime() + HOUR));
		await s.request("a");
		s.health.set(key(OPUS_55), new Error("usage endpoint down"));
		await s.request("b");

		expect(s.set).toEqual([
			["a", key(OPUS_55)],
			["b", key(OPUS_55)],
		]);
	});
});
