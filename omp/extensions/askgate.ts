// Entry of the AskGate pre-send gate; the behaviour is described and implemented in
// askgate-core.ts. This file binds the fork's objects the core needs — the completion, the
// gate role's retry.fallbackChains chain, and buildSessionContext for the primary's context — so the
// core imports nothing from the fork and runs under `bun test` with fakes. (Omp's loader would
// resolve the fork's packages from any module the entry imports; keeping them here is for the tests,
// not for the loader.) A transient provider failure gets one retry inside the gate's own deadline; a
// model that still fails hands the request to the next model of the chain, which the core walks.
// The chain comes from omp's own resolution (`resolveRetryFallbackChainKey` and
// `findRetryFallbackCandidates`, walked depth-first into each fallback's own chain as the eval
// `completion()` bridge walks it), because `ctx.models.resolve` returns the role's first model only.
// The block/warn mode, when OMP_ASKGATE is unset, is the session's `askgate_block` arm, read through
// the experiments extension's own pure resolution (`resolveGates` over its gates.json and the
// environment, then `resolve` for the session's root id), so both extensions agree on the arm and
// the `experiments` entry the other one records names it. A bad gate throws here, as it does there.
// The installer links only this file; askgate-core.ts, the prompt files, ./experiments/gates.* and
// ../watchdog/askgate.md resolve from its real path.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { buildSessionContext, type ExtensionAPI, type ReadonlySessionManager, settings } from "@oh-my-pi/pi-coding-agent";
import { cfgDisabledProviders } from "@oh-my-pi/pi-coding-agent/config/model-settings";
import { resolveModelOverride } from "@oh-my-pi/pi-coding-agent/config/model-resolver";
import { EXTENSION_HANDLER_TIMEOUT_MS } from "@oh-my-pi/pi-coding-agent/extensibility/extensions/runner";
import { cfgExtensionHandlersToolCallTimeoutMs } from "@oh-my-pi/pi-coding-agent/extensibility/settings";
import { findRetryFallbackCandidates, getRetryFallbackChains, type RetryFallbackResolutionContext, resolveRetryFallbackChainKey } from "@oh-my-pi/pi-coding-agent/session/retry-fallback-chains";
import { cfgRetry } from "@oh-my-pi/pi-coding-agent/session/settings";
import { type Api, type ApiKeyResolver, type AssistantMessage, completeSimple, type Model, retryTransientCompletion } from "@oh-my-pi/pi-ai";
import { type CompleteRequest, type Completion, createAskGate, GATE_EFFORT, GATE_ROLE, type GateModel, type Message, spawnDryRun, type Usage } from "./askgate-core";
import gateFile from "./experiments/gates.json" with { type: "json" };
import { resolve, resolveGates, rootSessionId } from "./experiments/gates";

// The core passes the fork's objects through untyped: ctx is the ExtensionContext, whose modelRegistry and
// sessionManager this reads, and model is the full Model<Api> ctx.models.resolve handed back.
type Registry = {
	resolver: (model: Model<Api>, sessionId: string) => ApiKeyResolver;
	getAvailable: () => Model<Api>[];
	find: (provider: string, id: string) => Model<Api> | undefined;
	hasProvider: (provider: string) => boolean;
};
type Ctx = { modelRegistry: Registry; sessionManager: ReadonlySessionManager };

/** The models after `primary` in the gate role's chain, each followed by its own chain, deduplicated; none when model fallback is off. */
function fallbackModels(ctx: unknown, primary: GateModel): GateModel[] {
	const retry = cfgRetry.get(settings);
	if (!retry.enabled || !retry.modelFallback) return [];
	const registry = (ctx as Ctx).modelRegistry;
	const context: RetryFallbackResolutionContext = { chains: getRetryFallbackChains(settings), getModelRole: role => settings.getModelRole(role), modelLookup: registry };
	const disabled = new Set(cfgDisabledProviders.get(settings));
	const seen = new Set([`${primary.provider}/${primary.id}`]);
	const expanded = new Set<string>();
	const out: Model<Api>[] = [];
	const walk = (selector: string, model: Model<Api>, roleHint: string | undefined) => {
		if (expanded.has(selector)) return;
		expanded.add(selector);
		const chainKey = resolveRetryFallbackChainKey(context, selector, model, roleHint);
		if (!chainKey) return;
		for (const entry of findRetryFallbackCandidates(context, chainKey, selector, model, { allowMissingPrimary: true })) {
			const candidate = resolveModelOverride([entry.raw], registry, settings).model;
			if (!candidate || disabled.has(candidate.provider) || seen.has(`${candidate.provider}/${candidate.id}`)) continue;
			seen.add(`${candidate.provider}/${candidate.id}`);
			out.push(candidate);
			walk(entry.raw, candidate, undefined);
		}
	};
	walk(settings.getModelRole(GATE_ROLE) ?? `${primary.provider}/${primary.id}`, primary as Model<Api>, GATE_ROLE);
	return out;
}

async function complete({ ctx, model, system, user, sessionId, signal }: CompleteRequest): Promise<Completion> {
	const full = model as Model<Api>;
	// Every attempt bills: a transient retry, a resampled thinking loop, the aborted last one. onAttempt sees each.
	const usage: Usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, cost: 0 };
	const onAttempt = (attempt: AssistantMessage) => {
		usage.input += attempt.usage.input;
		usage.output += attempt.usage.output;
		usage.cacheRead += attempt.usage.cacheRead;
		usage.cacheWrite += attempt.usage.cacheWrite;
		usage.cost += attempt.usage.cost.total;
	};
	try {
		const message = await retryTransientCompletion(
			() =>
				completeSimple(
					full,
					{ systemPrompt: [system], messages: [{ role: "user", content: user, timestamp: Date.now() }] },
					// Caching off: the provider's breakpoints cover the whole prompt, transcript included, so every
					// call would write it all at the one-hour rate (twice the input price here), and the transcript,
					// which slides on every call, is never read back.
					{ apiKey: (ctx as Ctx).modelRegistry.resolver(full, sessionId), sessionId, reasoning: GATE_EFFORT, cacheRetention: "none", maxTokens: 1200, temperature: 0, signal, onAttempt },
				),
			{ maxAttempts: 2, provider: full.provider, signal },
		);
		const text = message.content.filter((b): b is { type: "text"; text: string } => b.type === "text").map(b => b.text).join("\n");
		return { text, error: message.stopReason === "error" ? (message.errorMessage ?? "error") : message.stopReason === "aborted" ? "aborted" : undefined, usage };
	} catch (error) {
		// An abort during a retry's backoff, or a thrown provider error: what the attempts spent is still owed.
		return { text: "", error: error instanceof Error ? error.message : String(error), usage };
	}
}

/** The runner's tool_call handler ceiling, normalised as the runner does (runner.ts normalizeHandlerTimeout). */
function handlerCeilingMs(): number {
	const configured = cfgExtensionHandlersToolCallTimeoutMs.get(settings);
	return Number.isFinite(configured) && configured > 0 ? configured : EXTENSION_HANDLER_TIMEOUT_MS;
}

/** Appends to the dump, owner-only on every open (not only on creation), and never through a symlink. */
function appendDump(p: string, text: string): void {
	const fd = fs.openSync(p, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
	try {
		fs.fchmodSync(fd, 0o600);
		fs.writeSync(fd, text);
	} finally {
		fs.closeSync(fd);
	}
}

function readFile(p: string): string | undefined {
	try {
		return fs.readFileSync(p, "utf8");
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
		throw error;
	}
}

export default function askgate(pi: ExtensionAPI): void {
	// Resolved at bind, as the experiments extension resolves them, so a bad gate refuses the load.
	const gates = resolveGates(gateFile, process.env);
	createAskGate({
		env: process.env,
		home: os.homedir(),
		now: Date.now,
		readFile,
		// The dump holds the primary system prompt and transcript: owner-only, like the session files.
		appendFile: appendDump,
		complete,
		fallbackModels,
		charterPath: path.join(path.dirname(fs.realpathSync(import.meta.path)), "..", "watchdog", "askgate.md"),
		handlerCeilingMs,
		dryRun: spawnDryRun,
		contextMessages: ctx => {
			const { sessionManager } = ctx as unknown as Ctx;
			return buildSessionContext(sessionManager.getEntries(), sessionManager.getLeafId()).messages as unknown as readonly Message[];
		},
		blockArm: ({ sessionManager }) => resolve(gates, rootSessionId(sessionManager.getSessionFile(), sessionManager.getSessionId())).features.askgate_block.on,
	})(pi);
}
