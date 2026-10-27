import { afterEach, describe, expect, test } from "bun:test";
import extension, { CREDENTIAL_ATTR, ORGANIZATION_ATTR } from "../span-account";

type Pi = Parameters<typeof extension>[0];
type Account = { credentialId: number; accountId?: string };
type Handler = (event: unknown, ctx: unknown) => unknown;

const OTEL_API = Symbol.for("opentelemetry.js.api.1");
const SPAN_KEY = Symbol.for("OpenTelemetry Context Key SPAN");
const ORG = "5fd73c3e-0000-4000-8000-000000000001";

/** A span recording its attributes, made active the way OpenTelemetry's global context manager reports it. */
function activate(name: string) {
	const attributes: Record<string, string> = {};
	const span = { name, setAttribute: (key: string, value: string) => (attributes[key] = value) };
	(globalThis as Record<symbol, unknown>)[OTEL_API] = {
		context: { active: () => ({ getValue: (key: symbol) => (key === SPAN_KEY ? span : undefined) }) },
	};
	return attributes;
}

/** The extension bound to a store whose OAuth logins per provider come from `accounts`. */
function bind(accounts: (provider: string) => Account[]) {
	const handlers = new Map<string, Handler>();
	const debug: unknown[] = [];
	const pi = {
		on: (event: string, handler: Handler) => handlers.set(event, handler),
		logger: { debug: (...args: unknown[]) => debug.push(args) },
	} as unknown as Pi;
	extension(pi);
	const ctx = { modelRegistry: { authStorage: { oauth: { accounts } } } };
	return {
		debug,
		/** The provider's response arrived with `headers`. */
		respond: (headers: Record<string, string>) => handlers.get("after_provider_response")?.({ type: "after_provider_response", headers }, ctx),
		/** The turn finalized; omp stamped the serving credential's row id on it (absent for a key from no stored credential). */
		finish: (provider: string, credentialId?: number) =>
			handlers.get("assistant_message")?.({ type: "assistant_message", message: { role: "assistant", provider, credentialId, content: [] } }, ctx),
	};
}

const LOGINS = (provider: string): Account[] =>
	provider === "anthropic"
		? [
				{ credentialId: 3, accountId: "acct-a" },
				{ credentialId: 4, accountId: "acct-b" },
				{ credentialId: 9, accountId: "c@example.com" },
				{ credentialId: 5 },
			]
		: [];

afterEach(() => {
	delete (globalThis as Record<symbol, unknown>)[OTEL_API];
});

describe("span-account", () => {
	test("labels the chat span with the organization and the OAuth account that served the turn", () => {
		const span = activate("chat claude-opus-5-5");
		const session = bind(LOGINS);
		session.respond({ "anthropic-organization-id": ORG });
		session.finish("anthropic", 4);

		expect(span).toEqual({ [ORGANIZATION_ATTR]: ORG, [CREDENTIAL_ATTR]: "account.acct-b" });
	});

	test("reads the organization LiteLLM forwards", () => {
		const span = activate("chat claude-opus-5-5");
		bind(LOGINS).respond({ "llm_provider-anthropic-organization-id": ORG });

		expect(span[ORGANIZATION_ATTR]).toBe(ORG);
	});

	test("names the login that served each turn, whichever the session rotated to", () => {
		const session = bind(LOGINS);
		const first = activate("chat claude-opus-5-5");
		session.finish("anthropic", 3);
		const second = activate("chat claude-opus-5-5");
		session.finish("anthropic", 4);

		expect([first[CREDENTIAL_ATTR], second[CREDENTIAL_ATTR]]).toEqual(["account.acct-a", "account.acct-b"]);
	});

	test.each<[string, string, number | undefined, string | undefined]>([
		["an account id that is an email address", "anthropic", 9, "credential.9"],
		["an OAuth login with no account id", "anthropic", 5, "credential.5"],
		["a stored API key (no OAuth login has the row)", "anthropic", 12, "credential.12"],
		["a key from no stored credential", "anthropic", undefined, undefined],
	])("labels the credential for %s", (_case, provider, credentialId, expected) => {
		const span = activate("chat claude-opus-5-5");
		bind(LOGINS).finish(provider, credentialId);

		expect(span[CREDENTIAL_ATTR]).toBe(expected);
	});

	test("leaves a span that is not a chat span alone", () => {
		const span = activate("execute_tool bash");
		const session = bind(LOGINS);
		session.respond({ "anthropic-organization-id": ORG });
		session.finish("anthropic", 4);

		expect(span).toEqual({});
	});

	test("does nothing with telemetry off, and a failing auth store costs only the credential label", () => {
		const off = bind(LOGINS);
		off.respond({ "anthropic-organization-id": ORG });
		off.finish("anthropic", 4);
		expect(off.debug).toEqual([]);

		const span = activate("chat claude-opus-5-5");
		const session = bind(() => {
			throw new Error("store closed");
		});
		session.respond({ "anthropic-organization-id": ORG });
		session.finish("anthropic", 4);
		expect(span).toEqual({ [ORGANIZATION_ATTR]: ORG });
		expect(session.debug).toHaveLength(1);
	});
});
