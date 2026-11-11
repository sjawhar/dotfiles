// Label each chat span omp exports with the account that paid for it and the session that made the call.
//
// Spend is priced from omp's OTEL chat spans (scripts/agent-spend-env.sh exports them), and one machine
// holds several logins that omp rotates between. Without these attributes the spend pipeline could tell
// accounts apart only by reading the credential store (dispatch://INFRA-165), and could not group spend
// by the kind of agent or the top-level session:
//   * `omp.gen_ai.auth.organization_id`: the organization the request's key or token belongs to, from
//     the `anthropic-organization-id` response header (LiteLLM forwards it as
//     `llm_provider-anthropic-organization-id`); no header, no attribute. Set on `after_provider_response`.
//   * `omp.gen_ai.auth.credential`: the stored credential that produced the turn, named by the finalized
//     message's `credentialId` (omp stamps the row of the attempt that served, after any rotation):
//     `account.<id>` when that row is an OAuth login with an account id (Anthropic's account uuid; for
//     Codex the ChatGPT workspace id, which seats of one workspace share; one that is an email address is
//     never used), else `credential.<row id>`;
//     unset for a key from no stored credential (a models.yml `apiKey`, an env var, `--api-key`).
//     Set on `assistant_message`.
//   * `omp.agent.type`: the agent definition name from `ctx.agent.name` (`main` for a top-level session).
//   * `omp.session.root`: the root session id from `ctx.sessionManager`: the first `Z_<uuid>` in its
//     transcript path, or the session's own id when it has no transcript (`--no-session`).
//     Both are set on `after_provider_response`, where omp fires for every provider response inside the
//     chat span; values outside the spend collector's character and 255-character limits are omitted.
//
// How: omp's agent loop runs the provider call, and then its `assistant_message` hook, with the chat span
// active (`runInActiveSpan`), and ends the span only after both. The agent type and root use the provider
// response hook rather than `assistant_message`, which omp skips on an aborted turn. The handlers read the
// active span through OpenTelemetry's process-global registry, the documented way separate copies of
// `@opentelemetry/api` share one tracer and context (`Symbol.for("opentelemetry.js.api.1")`; the span
// context key is `Symbol.for("OpenTelemetry Context Key SPAN")`), because omp serves extensions only its
// own `@oh-my-pi/*` packages. The account id comes from `authStorage.oauth.accounts(provider)`, a read-only
// listing that reads no token. Both handlers are synchronous, return nothing (no rewrite), and swallow
// every error into one debug line, so they can neither slow nor fail a request.
//
// Coverage: agent-loop provider responses from top-level sessions and subagents get the agent type and
// root id, except for a call that ends before a provider response. The organization needs a provider that
// reports response headers to omp (Anthropic, OpenAI Completions/Responses; not Codex), and an aborted turn
// gets no credential label (omp skips `assistant_message` on abort). Advisors and handoff summaries get
// only the response-hook labels; compaction and branch summaries get neither; side turns (`/btw`, ephemeral
// turns) open no chat span. With telemetry export off there is no active span and nothing happens. The
// spend collector must keep both `omp.agent.type` and `omp.session.root`. End-to-end check against a real
// omp binary: `__tests__/span-account-e2e/run.sh`.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { rootSessionId } from "./experiments/gates";

export const ORGANIZATION_ATTR = "omp.gen_ai.auth.organization_id";
export const CREDENTIAL_ATTR = "omp.gen_ai.auth.credential";
export const AGENT_TYPE_ATTR = "omp.agent.type";
export const ROOT_SESSION_ATTR = "omp.session.root";
const OTEL_API = Symbol.for("opentelemetry.js.api.1");
const SPAN_KEY = Symbol.for("OpenTelemetry Context Key SPAN");
const COLLECTOR_VALUE = /^[A-Za-z0-9._@+-]{1,255}$/;
const collectorValue = (value: unknown) => (typeof value === "string" && COLLECTOR_VALUE.test(value) ? value : undefined);

interface ActiveSpan {
	readonly name?: string;
	setAttribute(key: string, value: string): unknown;
}

interface OtelGlobal {
	context?: { active(): { getValue(key: symbol): unknown } };
}

interface OAuthAccount {
	credentialId: number;
	accountId?: string;
}

/** The active span when it is one of omp's `chat <model>` spans. */
function activeChatSpan(): ActiveSpan | undefined {
	const api = (globalThis as Record<symbol, OtelGlobal | undefined>)[OTEL_API];
	const span = api?.context?.active().getValue(SPAN_KEY) as ActiveSpan | undefined;
	if (!span || typeof span.setAttribute !== "function") return undefined;
	return typeof span.name === "string" && !span.name.startsWith("chat ") ? undefined : span;
}

export default function (pi: ExtensionAPI) {
	const skipped = (error: unknown) =>
		pi.logger.debug("span-account: span not labelled", { error: error instanceof Error ? error.message : String(error) });

	pi.on("after_provider_response", (event, ctx) => {
		try {
			const span = activeChatSpan();
			const organizationId =
				event.headers["anthropic-organization-id"]?.trim() || event.headers["llm_provider-anthropic-organization-id"]?.trim();
			if (span && organizationId) span.setAttribute(ORGANIZATION_ATTR, organizationId);
			if (!span) return;
			const agentType = collectorValue(ctx.agent.name);
			const sessionManager = ctx.sessionManager;
			const rootId = collectorValue(rootSessionId(sessionManager.getSessionFile(), sessionManager.getSessionId()));
			if (agentType) span.setAttribute(AGENT_TYPE_ATTR, agentType);
			if (rootId) span.setAttribute(ROOT_SESSION_ATTR, rootId);
		} catch (error) {
			skipped(error);
		}
	});

	pi.on("assistant_message", (event, ctx) => {
		try {
			const { credentialId, provider } = event.message;
			if (credentialId === undefined) return;
			const span = activeChatSpan();
			if (!span) return;
			const accounts: OAuthAccount[] = ctx.modelRegistry.authStorage.oauth.accounts(provider);
			const accountId = accounts.find(account => account.credentialId === credentialId)?.accountId?.trim();
			span.setAttribute(CREDENTIAL_ATTR, accountId && !accountId.includes("@") ? `account.${accountId}` : `credential.${credentialId}`);
		} catch (error) {
			skipped(error);
		}
	});
}
