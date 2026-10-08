// Record when a turn was answered by a different model than the session asked for.
//
// Anthropic can serve a declined request from a fallback model inside the same request, and then
// route that conversation straight to the fallback for about an hour. omp already sends the
// `fallbacks` parameter for the Fable and Mythos families, so this happens on this machine today:
// two runs on 2026-10-07 configured for Fable 5.1 were both answered by Opus 5, with no refusal
// reaching omp and no model change in the session. Nothing recorded it. The session's own
// attribution still named Fable, and the only trace was the `model` field on the individual
// message — which is how it ran unnoticed from September (dispatch://AGENTC-319).
//
// This writes a `served-by-other-model` session entry when it happens, so a downgraded session is
// visible rather than silent, and so the survey that measures refusal cost can see these turns at
// all. Two signals, because they cover different cases:
//   * an Anthropic `fallback` content block, which marks the handoff inside one request;
//   * the message's model differing from the model the session is on, which is what a turn routed
//     straight to the fallback looks like, since Anthropic sends no handoff block for one.
//
// A turn omp itself moved is not this: there the session's own model has already changed to match,
// so the comparison finds nothing. A refused turn is skipped — it is already recorded as an error.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	pi.on("message_end", async (event, ctx) => {
		const message = event.message;
		if (message.role !== "assistant") return undefined;
		// Already visible as an error turn; its model is the one that declined, not one that served.
		if (message.stopDetails?.type === "refusal") return undefined;

		const handoff = message.content.find(block => block.type === "fallback");
		const asked = ctx.models.current();
		const drifted = asked !== undefined && message.model !== asked.id;
		if (!handoff && !drifted) return undefined;

		pi.appendEntry("served-by-other-model", {
			agent: `${ctx.agent.kind}:${ctx.agent.name}`,
			asked: asked ? `${asked.provider}/${asked.id}` : undefined,
			served: `${message.provider}/${message.model}`,
			// Present only when Anthropic switched inside this request; absent on a routed turn.
			handoff: handoff ? `${handoff.from.model} -> ${handoff.to.model}` : undefined,
			at: new Date().toISOString(),
		});
		return undefined;
	});
}
