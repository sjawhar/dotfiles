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
//   * It asks the auth store for the configured model's usage health before restoring. The same
//     event fires when omp's usage preflight moves a session off a provider whose quota window is
//     spent (the Codex 7-day window, for one). Restoring then sent one request to a model that
//     cannot serve, the preflight moved the session back, and each round trip rendered the system
//     prompt twice, so Anthropic rewrote the whole cached context twice an hour per session: 649
//     retries and about $950 of cache writes in six hours on 2026-10-24 (dispatch://INFRA-262).
//     A depleted or reserve answer waits until the earliest reset the store reports, or another
//     cooldown when it reports none; an unknown or failed answer restores as before.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

/** Matches the window Anthropic documents for its own post-refusal routing. */
const COOLDOWN_MS = Number(process.env.OMP_REFUSAL_COOLDOWN_MS ?? 60 * 60 * 1000);
/** omp's default `retry.usageReservePct`, the margin its usage preflight falls back at. */
const RESERVE_FRACTION = 0.1;

interface Armed {
	/** The selector the fallback moved the session off, as `provider/id`. */
	restoreTo: string;
	/** Earliest time to try restoring. */
	due: number;
}

type UsageHealth = { state: string; accounts: Array<{ resetsAt?: number }> };
type HealthCtx = {
	sessionManager?: { getSessionId?(): string };
	modelRegistry?: {
		authStorage?: {
			health?: {
				model(
					provider: string,
					options: { modelId?: string; sessionId?: string; baseUrl?: string; reserveFraction: number },
				): Promise<UsageHealth>;
			};
		};
	};
};

/** When the target can be restored: now, or the time to look again because its quota is spent. */
async function nextUsableAt(
	ctx: HealthCtx,
	target: { provider: string; id: string; baseUrl?: string },
	now: number,
): Promise<number> {
	const health = ctx.modelRegistry?.authStorage?.health;
	if (!health) return now;
	let report: UsageHealth;
	try {
		report = await health.model(target.provider, {
			modelId: target.id,
			sessionId: ctx.sessionManager?.getSessionId?.(),
			baseUrl: target.baseUrl,
			reserveFraction: RESERVE_FRACTION,
		});
	} catch {
		return now;
	}
	if (report.state !== "depleted" && report.state !== "reserve") return now;
	let reset = Infinity;
	for (const account of report.accounts) {
		if (account.resetsAt !== undefined && account.resetsAt > now) reset = Math.min(reset, account.resetsAt);
	}
	return Number.isFinite(reset) ? reset : now + COOLDOWN_MS;
}

export default function (pi: ExtensionAPI) {
	// Keyed by agent registry id: a subagent walks its own chain and must not read the parent's.
	const armed = new Map<string, Armed>();

	pi.on("retry_fallback_applied", async (event, ctx) => {
		// Keep the first arming: a chain that walks several models within one turn should restore
		// to the model the session started on, not to the middle of the chain.
		if (!armed.has(ctx.agent.id)) {
			armed.set(ctx.agent.id, { restoreTo: event.from, due: Date.now() + COOLDOWN_MS });
		}
		return undefined;
	});

	pi.on("context", async (_event, ctx) => {
		const pending = armed.get(ctx.agent.id);
		const now = Date.now();
		if (!pending || now < pending.due) return undefined;

		const current = ctx.models.current();
		if (current && `${current.provider}/${current.id}` === pending.restoreTo) {
			armed.delete(ctx.agent.id);
			return undefined;
		}
		// Gone from this session's roster (credential lost, catalog change): nothing to restore to.
		const target = ctx.models.resolve(pending.restoreTo);
		if (!target) {
			armed.delete(ctx.agent.id);
			return undefined;
		}
		const usableAt = await nextUsableAt(ctx as unknown as HealthCtx, target, now);
		if (usableAt > now) {
			pending.due = usableAt;
			return undefined;
		}
		armed.delete(ctx.agent.id);
		await pi.setModel(target);
		return undefined;
	});
}
