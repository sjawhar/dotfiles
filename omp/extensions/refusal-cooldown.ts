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
//   * It arms only on the session's own fallback (fallback-event.ts). omp emits the same event
//     when an advisor's model fails over, naming the advisor's model as `from`; arming on that
//     would, an hour later, switch the session itself onto the advisor's model (Sonnet 5.5 here).
//   * It restores from the `context` hook, which runs before every provider request. omp checks
//     its own restore only when a new prompt arrives or after an auto-continue, which a long
//     unattended tool loop never reaches — that gap is why this is an extension and not a setting.
//   * Switching the model from this hook was verified not to disturb the request already in
//     flight: that request completes on the old model and the next one uses the restored model.
//   * A re-refusal needs no special handling: the chain moves the session down again and emits a
//     new `retry_fallback_applied`, which re-arms the cooldown from that moment (keeping the model
//     to restore while the event continues the same walk: its entry directly follows the armed one).
//   * A person's choice during the cooldown wins. omp itself treats an explicit model change as the
//     end of a fallback. The arming is tied to the fallback's own `model_change` entry: every other
//     model switch (omp's own restore, /model, another session resumed in this process) appends a
//     newer one, and at the due time a session whose newest entry is not the armed one is left
//     alone. The effort is put back only if it is still the one the fallback left and the effort
//     configured before the fallback was not `auto` (which `from` records only as resolved).
//   * The Fireworks Fast degrade is not armed: omp keeps the model and drops only the tier that just
//     rejected the session, and restoring it would fail again every hour. A fallback off a model omp
//     started the session on in place of the configured one is armed like any other and restores
//     that replacement: omp's own restore toward the configured model is often pinned or absent, and
//     the replacement ranks above the model the fallback reached (served-model-notice announces it).
//   * It asks the auth store for the configured model's usage health before restoring. The same
//     event fires when omp's usage preflight moves a session off a provider whose quota window is
//     spent (the Codex 7-day window, for one). Restoring then sent one request to a model that
//     cannot serve, the preflight moved the session back, and each round trip rendered the system
//     prompt twice, so Anthropic rewrote the whole cached context twice an hour per session: 649
//     retries and about $950 of cache writes in six hours on 2026-10-24 (dispatch://INFRA-262).
//     A depleted or reserve answer waits until the earliest reset the store reports, or another
//     cooldown when it reports none; an unknown or failed answer restores as before.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { type FallbackCtx, newestModelChangeId, ownFallbackClaims } from "./fallback-event";

/** The effort suffix omp writes on `retry_fallback_applied`'s `from` (`anthropic/claude-opus-5-5:xhigh`). */
const EFFORT_SUFFIX = /:(off|minimal|low|medium|high|xhigh|max)$/;
/** Matches the window Anthropic documents for its own post-refusal routing. */
const COOLDOWN_MS = Number(process.env.OMP_REFUSAL_COOLDOWN_MS ?? 60 * 60 * 1000);
/** omp's default `retry.usageReservePct`, the margin its usage preflight falls back at. */
const RESERVE_FRACTION = 0.1;

interface Armed {
	/** The selector the fallback moved the session off, as `provider/id[:effort]`. */
	restoreTo: string;
	/** Earliest time to try restoring. */
	due: number;
	/** The `model_change` entry of the latest fallback step; any newer one ends the arming. */
	entryId: string;
	/** The effort the session had right after that step. */
	fallbackLevel: string | undefined;
	/** Whether the effort configured before the walk was `auto`, which is left to omp. */
	autoEffort: boolean;
}

const sameModel = (a: { provider: string; id: string } | undefined, b: { provider: string; id: string } | undefined) =>
	!!a && !!b && a.provider === b.provider && a.id === b.id;

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
	const own = ownFallbackClaims();

	pi.on("retry_fallback_applied", async (event, ctx) => {
		if (event.role === "fireworks-fast") return undefined;
		const fallback = own(event, ctx as unknown as FallbackCtx);
		if (!fallback) return undefined;
		const previous = armed.get(ctx.agent.id);
		// A step that continues the same walk keeps the model the session started on; a fallback after
		// any other model switch starts a new arming.
		const continues = previous !== undefined && fallback.previousEntryId === previous.entryId;
		armed.set(ctx.agent.id, {
			restoreTo: continues ? previous.restoreTo : event.from,
			due: Date.now() + COOLDOWN_MS,
			entryId: fallback.entryId,
			fallbackLevel: pi.getThinkingLevel(),
			autoEffort: continues ? previous.autoEffort : fallback.autoEffort,
		});
		return undefined;
	});

	pi.on("context", async (_event, ctx) => {
		const pending = armed.get(ctx.agent.id);
		const now = Date.now();
		if (!pending || now < pending.due) return undefined;

		// `from` carries the effort suffix (`anthropic/claude-opus-5-5:xhigh`), so compare resolved models.
		// Gone from this session's roster (credential lost, catalog change): nothing to restore to.
		const target = ctx.models.resolve(pending.restoreTo);
		const current = ctx.models.current();
		// Any model switch since the fallback (omp's own restore, /model, another session): not ours to undo.
		const superseded = newestModelChangeId(ctx as unknown as FallbackCtx) !== pending.entryId;
		if (!target || !current || superseded || sameModel(current, target)) {
			armed.delete(ctx.agent.id);
			return undefined;
		}
		const usableAt = await nextUsableAt(ctx as unknown as HealthCtx, target, now);
		if (usableAt > now) {
			pending.due = usableAt;
			return undefined;
		}
		armed.delete(ctx.agent.id);
		const levelBefore = pi.getThinkingLevel();
		if (!(await pi.setModel(target))) return undefined;
		// setModel keeps the fallback's effort (a chain entry such as `…:low` sets its own), so put back
		// the effort the session fell back from, which `from` names (proven on omp 18.13.3, 2026-10-29).
		const effort = EFFORT_SUFFIX.exec(pending.restoreTo)?.[1];
		if (effort && levelBefore === pending.fallbackLevel && !pending.autoEffort) {
			pi.setThinkingLevel(effort as Parameters<ExtensionAPI["setThinkingLevel"]>[0]);
		}
		return undefined;
	});
}
