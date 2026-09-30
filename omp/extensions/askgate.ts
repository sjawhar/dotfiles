// Entry of the AskGate pre-send gate; the behaviour is described and implemented in
// askgate-core.ts. This is the only file that imports the fork at runtime: a runtime-loaded
// extension resolves `@oh-my-pi/pi-ai` from its entry file only, so the one completion the gate
// makes is bound here and handed to the core. A transient provider failure gets one retry inside
// the gate's own deadline, and a failure never falls back to another model. The installer links
// only this file; askgate-core.ts, askgate-request.md and ../watchdog/askgate.md resolve from its
// real path.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { type Api, type ApiKeyResolver, completeSimple, type Model, retryTransientCompletion } from "@oh-my-pi/pi-ai";
import { type CompleteRequest, type Completion, createAskGate, GATE_EFFORT } from "./askgate-core";

// ctx.models.resolve hands back a full Model<Api>; the core only passes it through.
type Ctx = { modelRegistry: { resolver: (model: Model<Api>, sessionId: string) => ApiKeyResolver } };

async function complete({ ctx, model, system, user, sessionId, signal }: CompleteRequest): Promise<Completion> {
	const full = model as Model<Api>;
	const message = await retryTransientCompletion(
		() =>
			completeSimple(
				full,
				{ systemPrompt: [system], messages: [{ role: "user", content: user, timestamp: Date.now() }] },
				// Caching off: the provider's breakpoints cover the whole prompt, transcript included, so every
				// call would write it all at the one-hour rate (twice the input price here), and the transcript,
				// which slides on every call, is never read back.
				{ apiKey: (ctx as Ctx).modelRegistry.resolver(full, sessionId), sessionId, reasoning: GATE_EFFORT, cacheRetention: "none", maxTokens: 1200, temperature: 0, signal },
			),
		{ maxAttempts: 2, provider: full.provider, signal },
	);
	const text = message.content.filter((b): b is { type: "text"; text: string } => b.type === "text").map(b => b.text).join("\n");
	const { input, output, cacheRead, cacheWrite, cost } = message.usage;
	return {
		text,
		error: message.stopReason === "error" ? (message.errorMessage ?? "error") : message.stopReason === "aborted" ? "aborted" : undefined,
		usage: { input, output, cacheRead, cacheWrite, cost: cost.total },
	};
}

function readFile(p: string): string | undefined {
	try {
		return fs.readFileSync(p, "utf8");
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
		throw error;
	}
}

export default createAskGate({
	env: process.env,
	home: os.homedir(),
	now: Date.now,
	readFile,
	// The dump holds the primary system prompt and transcript: owner-only, like the session files (the mode applies on creation).
	appendFile: (p, text) => fs.appendFileSync(p, text, { mode: 0o600 }),
	complete,
	charterPath: path.join(path.dirname(fs.realpathSync(import.meta.path)), "..", "watchdog", "askgate.md"),
}) as (pi: ExtensionAPI) => void;
