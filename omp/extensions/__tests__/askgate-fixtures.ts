// The primary's context for askgate.test.ts: messages in the shapes the fork's buildSessionContext
// returns for a root session (packages/coding-agent/src/session/session-context.ts and the message
// constructors in packages/agent/src/compaction/messages.ts, omp 18.4.3-sami.20260929-152920), with
// synthetic text: no transcript content, issue key or host name appears here.
export type FixtureMessage = Record<string, unknown> & { role: string };

let seq = 0;
let callSeq = 0;

export const user = (text: string): FixtureMessage => ({ role: "user", content: [{ type: "text", text }], attribution: "user", timestamp: ++seq });

export type Block =
	| { type: "text"; text: string }
	| { type: "thinking"; thinking: string }
	| { type: "toolCall"; name: string; arguments: Record<string, unknown> };
export function assistant(...blocks: Block[]): FixtureMessage {
	const content = blocks.map(b =>
		b.type === "thinking"
			? { type: "thinking", thinking: b.thinking, thinkingSignature: "c2lnbmF0dXJl" }
			: b.type === "toolCall"
				? { type: "toolCall", id: `toolu_${++callSeq}`, name: b.name, arguments: b.arguments, intent: "Doing the thing" }
				: b,
	);
	return {
		role: "assistant",
		content,
		api: "anthropic-messages",
		provider: "anthropic",
		model: "claude-example",
		usage: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 15, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
		stopReason: "toolUse",
		timestamp: ++seq,
	};
}

export const toolResult = (toolName: string, text: string, isError = false): FixtureMessage => ({
	role: "toolResult",
	toolCallId: `toolu_${callSeq}`,
	toolName,
	content: [{ type: "text", text }],
	details: {},
	isError,
	timestamp: ++seq,
});

export const bashExecution = (command: string, output: string): FixtureMessage => ({
	role: "bashExecution",
	command,
	output,
	exitCode: 0,
	cancelled: false,
	truncated: false,
	timestamp: ++seq,
	excludeFromContext: false,
});

/** A harness notice; its content is a string array in real sessions. */
export const developer = (text: string): FixtureMessage => ({
	role: "developer",
	content: [`<system-reminder>\n${text}\n</system-reminder>`],
	attribution: "agent",
	timestamp: ++seq,
});

/** A Dispatch answer as pi-envoy delivers it. Real ones display; a hidden one must render the same. */
export const envoyMessage = (body: string, display = true): FixtureMessage => ({
	role: "custom",
	customType: "envoy-message",
	content: `envoy:\n  to: you (0000…)\n  from: dispatch\n  at: "2026-09-30T00:00:00Z"\n  id: example-1\n---\n${body}`,
	display,
	timestamp: ++seq,
	attribution: "agent",
});

export const advisorCard = (note: string): FixtureMessage => ({
	role: "custom",
	customType: "advisor",
	content: `<advisory advisor="Example" guidance="weigh, don't blindly obey">\n${note}\n</advisory>`,
	display: true,
	details: { notes: [{ note }] },
	timestamp: ++seq,
	attribution: "agent",
});

export const hiddenCustomMessage = (customType: string, text: string): FixtureMessage => ({ role: "custom", customType, content: text, display: false, timestamp: ++seq });

export const compactionSummary = (summary: string): FixtureMessage => ({ role: "compactionSummary", summary, tokensBefore: 800_000, timestamp: ++seq });

export const unknownMessage = (): FixtureMessage => ({ role: "some_future_role", payload: { anything: true }, timestamp: ++seq });

/** About 1 MiB of context: turns of an assistant read call and a 6 KiB result, then the newest turns the window test reads. */
export function bigContext(): FixtureMessage[] {
	const turns: FixtureMessage[] = [compactionSummary("Earlier work, summarized."), user("Start the example task.")];
	for (let i = 0; turns.length < 400; i++) {
		turns.push(assistant({ type: "thinking", thinking: `Considering step ${i}.` }, { type: "toolCall", name: "read", arguments: { path: `/work/example/file-${i}.txt` } }));
		turns.push(toolResult("read", `line of file ${i}\n`.repeat(400)));
	}
	return [
		...turns,
		envoyMessage("Tester here: proceed with the comment, visible.", true),
		envoyMessage("Tester here: a hidden answer that must still reach the gate.", false),
		advisorCard(`An earlier advisory note that is long enough to be cut. ${"x".repeat(300)}`),
		developer("You stopped with 2 incomplete todo items."),
		assistant({ type: "text", text: "NEWEST MESSAGE: posting the comment now." }, { type: "toolCall", name: "write", arguments: { path: "xd://dispatch_comment", content: '{"issue":"X-1","body":"b"}' } }),
	];
}
