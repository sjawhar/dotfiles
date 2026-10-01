// Binding of the judge-difficulty log; the behaviour is described and implemented in
// judge-log-core.ts. This file binds the fork's completion (completeSimple through
// retryTransientCompletion and the model registry's key resolver, the askgate.ts pattern) and the
// log's path, so the core imports nothing from the fork and runs under `bun test` with fakes. A
// transient provider failure gets one retry, and a failure never falls back to another model.
// The experiments extension (index.ts) registers it under its `judge_log` gate.
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { type Api, type ApiKeyResolver, completeSimple, type Model, retryTransientCompletion } from "@oh-my-pi/pi-ai";
import { type CompleteRequest, type Completion, createJudgeLog } from "./judge-log-core";

// ctx is the ExtensionContext, whose modelRegistry this reads; model is the full Model<Api> ctx.models.resolve handed back.
type Ctx = { modelRegistry: { resolver: (model: Model<Api>, sessionId: string) => ApiKeyResolver } };

const LOG = path.join(os.homedir(), ".omp", "judge", "ratings.jsonl");

async function complete({ ctx, model, system, user, sessionId, signal }: CompleteRequest): Promise<Completion> {
	const full = model as Model<Api>;
	const message = await retryTransientCompletion(
		() =>
			completeSimple(
				full,
				{ systemPrompt: [system], messages: [{ role: "user", content: user, timestamp: Date.now() }] },
				// Caching off: every rating is a different assignment, so a cache write is never read back.
				{ apiKey: (ctx as Ctx).modelRegistry.resolver(full, sessionId), sessionId, cacheRetention: "none", maxTokens: 200, temperature: 0, signal },
			),
		{ maxAttempts: 2, provider: full.provider, signal },
	);
	const text = message.content.filter((b): b is { type: "text"; text: string } => b.type === "text").map(b => b.text).join("\n");
	return { text, error: message.stopReason === "error" ? (message.errorMessage ?? "error") : message.stopReason === "aborted" ? "aborted" : undefined };
}

/** Appends to the log, which holds assignment text: owner-only, like the session files. */
async function append(line: string): Promise<void> {
	await fs.mkdir(path.dirname(LOG), { recursive: true, mode: 0o700 });
	await fs.appendFile(LOG, line, { mode: 0o600 });
}

export default function judgeLog(pi: ExtensionAPI): void {
	createJudgeLog({ complete, append, now: Date.now })(pi);
}
