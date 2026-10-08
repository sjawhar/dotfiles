// Put a session back on its configured model an hour after a fallback pushed it down the chain.
//
// omp applies a classifier-refusal fallback pinned (turn-recovery.ts, `pinFallback:
// classifierRefusal`), and the one code path that would restore the original model returns early
// for anything pinned, so the session finishes its work on the weaker model. Measured over the 21
// days to 2026-10-07: 56 sessions fell back, and after a refusal a session ran a median of 110
// further turns on the weaker model (dispatch://AGENTC-319). Going back is nearly always safe — of
// 21 sessions that happened to return, 18 were answered normally and 3 were declined again and
// simply fell back again — and a `cyber` decline before any output is not billed, so the retry
// costs latency only.
//
// Why it is built this way:
//   * It arms on `retry_fallback_applied`, which names the model that was replaced. A model the
//     user chose with /model never fires that event, so this never reverts a deliberate choice.
//   * It does not filter that event down to refusals: the refusal call site passes no `reason`,
//     so the event carries nothing that identifies one. Arming on every fallback is harmless,
//     because omp already restores the unpinned ones itself on its own cooldown and both paths
//     restore the same model. This exists for the pinned refusal case, which omp never restores,
//     and for the gap below.
//   * It restores from the `context` hook, which runs before every provider request. omp checks
//     its own restore only when a new prompt arrives or after an auto-continue, which a long
//     unattended tool loop never reaches — that gap is why this is an extension and not a setting.
//   * Switching the model from this hook was verified not to disturb the request already in
//     flight: that request completes on the old model and the next one uses the restored model.
//   * A re-refusal needs no special handling: the chain moves the session down again and emits a
//     new `retry_fallback_applied`, which re-arms the cooldown from that moment.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

/** Matches the window Anthropic documents for its own post-refusal routing. */
const COOLDOWN_MS = Number(process.env.OMP_REFUSAL_COOLDOWN_MS ?? 60 * 60 * 1000);

interface Armed {
	/** The selector the fallback moved the session off, as `provider/id`. */
	restoreTo: string;
	at: number;
}

export default function (pi: ExtensionAPI) {
	// Keyed by agent registry id: a subagent walks its own chain and must not read the parent's.
	const armed = new Map<string, Armed>();

	pi.on("retry_fallback_applied", async (event, ctx) => {
		// Keep the first arming: a chain that walks several models within one turn should restore
		// to the model the session started on, not to the middle of the chain.
		if (!armed.has(ctx.agent.id)) {
			armed.set(ctx.agent.id, { restoreTo: event.from, at: Date.now() });
		}
		return undefined;
	});

	pi.on("context", async (_event, ctx) => {
		const pending = armed.get(ctx.agent.id);
		if (!pending || Date.now() - pending.at < COOLDOWN_MS) return undefined;
		armed.delete(ctx.agent.id);

		const current = ctx.models.current();
		if (current && `${current.provider}/${current.id}` === pending.restoreTo) return undefined;
		// Gone from this session's roster (credential lost, catalog change): nothing to restore to.
		const target = ctx.models.resolve(pending.restoreTo);
		if (!target) return undefined;
		await pi.setModel(target);
		return undefined;
	});
}
