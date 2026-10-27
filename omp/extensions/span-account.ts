// Label each chat span omp exports with the Anthropic organization and the stored login that served it.
//
// Spend is priced from omp's OTEL chat spans (scripts/agent-spend-env.sh exports them), and one machine
// holds several logins that omp rotates between. Without these two attributes the spend pipeline could
// tell accounts apart only by reading the credential store (dispatch://INFRA-165):
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
//
// How: omp's agent loop runs the provider call, and then its `assistant_message` hook, with the chat span
// active (`runInActiveSpan`), and ends the span only after both. The handlers read the active span through
// OpenTelemetry's process-global registry, the documented way separate copies of `@opentelemetry/api`
// share one tracer and context (`Symbol.for("opentelemetry.js.api.1")`; the span context key is
// `Symbol.for("OpenTelemetry Context Key SPAN")`), because omp serves extensions only its own `@oh-my-pi/*`
// packages. The account id comes from `authStorage.oauth.accounts(provider)`, a read-only listing that
// reads no token. Both handlers are synchronous, return nothing (no rewrite), and swallow every error into
// one debug line, so they can neither slow nor fail a request.
//
// Coverage: agent-loop turns of top-level sessions and subagents get both, except that the organization
// needs a provider that reports response headers to omp (Anthropic, OpenAI Completions/Responses; not
// Codex), and an aborted turn gets no credential label (omp skips `assistant_message` on abort). Advisors
// and handoff summaries get only the organization (their own `assistant_message` path, or none);
// compaction and branch summaries get neither; side turns (`/btw`, ephemeral turns) open no chat span.
// With telemetry export off there is no active span and nothing happens. The spend collector must keep
// both attributes. End-to-end check against a real omp binary: `__tests__/span-account-e2e/run.sh`.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export const ORGANIZATION_ATTR = "omp.gen_ai.auth.organization_id";
export const CREDENTIAL_ATTR = "omp.gen_ai.auth.credential";
const OTEL_API = Symbol.for("opentelemetry.js.api.1");
const SPAN_KEY = Symbol.for("OpenTelemetry Context Key SPAN");

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

	pi.on("after_provider_response", event => {
		try {
			const span = activeChatSpan();
			const organizationId =
				event.headers["anthropic-organization-id"]?.trim() || event.headers["llm_provider-anthropic-organization-id"]?.trim();
			if (span && organizationId) span.setAttribute(ORGANIZATION_ATTR, organizationId);
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
