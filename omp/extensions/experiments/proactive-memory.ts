// Binding of the proactive memory agent; the behaviour is described and implemented in
// proactive-memory-core.ts. This file binds the fork's objects the core needs — the completion
// (through completeSimple/retryTransientCompletion and the model registry's key resolver, the
// askgate.ts pattern), the injected TypeBox builder for tool schemas, and the clock/id/environment
// primitives — so the core imports nothing from the fork and runs under `bun test` with fakes.
// The experiments extension (index.ts) registers it under its `proactive_memory` gate.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { type Api, type ApiKeyResolver, type AssistantMessage, completeSimple, type Model, retryTransientCompletion, type Tool } from "@oh-my-pi/pi-ai";
import type * as TypeBox from "@oh-my-pi/omptype/typebox"; // the type of pi.typebox; the value is injected
import createProactiveMemory, { type CompleteRequest, type Completion, ID_ALPHABET, ID_LENGTH, mapStopReason, MEMORY_TEMPERATURE, type ToolSpec } from "./proactive-memory-core";

// The core passes the fork's objects through untyped: ctx is the ExtensionContext, whose
// modelRegistry this reads to resolve an API key for the memory model.
type Ctx = { modelRegistry: { resolver: (model: Model<Api>, sessionId: string) => ApiKeyResolver } };

function toTool(Type: typeof TypeBox.Type, spec: ToolSpec): Tool {
	return { name: spec.name, description: spec.description, parameters: Type.Object({ [spec.parameter]: Type.String({ description: spec.parameterDescription }) }) };
}

async function complete(ctx: Ctx, Type: typeof TypeBox.Type, request: CompleteRequest): Promise<Completion> {
	const model = request.model as Model<Api>;
	// Every attempt bills: a transient retry, a resampled thinking loop, the aborted last one. onAttempt sees each.
	const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
	const onAttempt = (attempt: AssistantMessage) => {
		usage.input += attempt.usage.input;
		usage.output += attempt.usage.output;
		usage.cacheRead += attempt.usage.cacheRead;
		usage.cacheWrite += attempt.usage.cacheWrite;
	};
	try {
		const message = await retryTransientCompletion(
			() =>
				completeSimple(
					model,
					{ systemPrompt: [request.system], messages: [{ role: "user", content: request.user, timestamp: Date.now() }], tools: request.tools?.map(spec => toTool(Type, spec)) },
					// Caching off: each memory step is a fresh single-shot call, not an extension of a growing
					// transcript, so there is nothing stable across calls for a cache breakpoint to cover.
					{
						apiKey: ctx.modelRegistry.resolver(model, request.sessionId),
						sessionId: `${request.sessionId}:memory`,
						temperature: MEMORY_TEMPERATURE,
						disableReasoning: true,
						cacheRetention: "none",
						maxTokens: request.maxTokens,
						toolChoice: request.tools ? "auto" : undefined,
						signal: request.signal,
						onAttempt,
					},
				),
			{ maxAttempts: 2, provider: model.provider, signal: request.signal },
		);
		const text = message.content.filter((b): b is { type: "text"; text: string } => b.type === "text").map(b => b.text).join("\n");
		const toolCalls = message.content
			.filter((b): b is { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown> } => b.type === "toolCall")
			.map(b => ({ name: b.name, arguments: b.arguments }));
		return { text, toolCalls, usage, error: mapStopReason(message.stopReason, message.errorMessage) };
	} catch (error) {
		// An abort during a retry's backoff, or a thrown provider error: what the attempts spent is still owed.
		return { text: "", toolCalls: [], usage, error: error instanceof Error ? error.message : String(error) };
	}
}

export default function proactiveMemory(pi: ExtensionAPI): void {
	createProactiveMemory({
		complete: request => complete(request.ctx as Ctx, pi.typebox.Type, request),
		now: Date.now,
		newId: () => Array.from(crypto.getRandomValues(new Uint8Array(ID_LENGTH)), b => ID_ALPHABET[b % ID_ALPHABET.length]).join(""),
		env: process.env,
	})(pi);
}
