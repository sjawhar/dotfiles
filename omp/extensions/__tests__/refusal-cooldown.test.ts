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

/** One process's binding of the extension, with a model per agent and the models it was set to. */
function bind(roster: Model[] = [OPUS_55, OPUS_5, OPUS_48]) {
	const handlers = new Map<string, (event: unknown, ctx: unknown) => Promise<unknown>>();
	const current = new Map<string, Model>();
	const set: Array<[string, string]> = [];
	let acting = "";
	const pi = {
		on: (event: string, handler: (event: unknown, ctx: unknown) => Promise<unknown>) => handlers.set(event, handler),
		setModel: async (model: Model) => {
			set.push([acting, key(model)]);
			current.set(acting, model);
		},
	} as unknown as Pi;
	extension(pi);
	const ctx = (agent: string) => ({
		agent: { id: agent },
		models: {
			current: () => current.get(agent),
			resolve: (selector: string) => roster.find(m => key(m) === selector),
		},
	});
	return {
		set,
		model: (agent: string) => key(current.get(agent) as Model),
		on: (agent: string, model: Model) => current.set(agent, model),
		/** The chain moved `agent` off `from` onto `to`. */
		fallback: async (agent: string, from: Model, to: Model) => {
			current.set(agent, to);
			await handlers.get("retry_fallback_applied")?.({ from: key(from), to: key(to) }, ctx(agent));
		},
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
});
