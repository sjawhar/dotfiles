// Session branch entries for askgate.test.ts. Each builder copies the shape of an entry type as a
// real root session file stores it (omp 18.4.3-sami.20260929-152920; the shapes are the fork's
// `SessionEntry` union, packages/coding-agent/src/session/session-entries.ts), with synthetic text:
// no transcript content, issue key or host name appears here.
export type FixtureEntry = Record<string, unknown> & { type: string };

let seq = 0;
let parent: string | null = null;
const at = () => new Date(Date.UTC(2026, 8, 30, 0, 0, seq)).toISOString();
function entry(fields: Record<string, unknown> & { type: string }): FixtureEntry {
	const id = (++seq).toString(16).padStart(8, "0");
	const e = { id, parentId: parent, timestamp: at(), ...fields };
	parent = id;
	return e;
}

/** The fixed-width title slot at the head of the file (carries no id). */
export const titleSlot = (): FixtureEntry => ({ type: "title", v: 1, title: "Example session", source: "auto", updatedAt: at(), pad: " ".repeat(40) });
/** The session header line. */
export const sessionHeader = (): FixtureEntry => ({ type: "session", version: 3, id: "00000000-0000-4000-8000-000000000000", timestamp: at(), cwd: "/work/example" });
export const modelChange = () => entry({ type: "model_change", model: "anthropic/claude-example", resolvedModelIsFallback: false });

export const user = (text: string) =>
	entry({ type: "message", message: { role: "user", content: [{ type: "text", text }], attribution: "user", timestamp: seq } });

export type Block =
	| { type: "text"; text: string }
	| { type: "thinking"; thinking: string }
	| { type: "toolCall"; name: string; arguments: Record<string, unknown> };
export function assistant(...blocks: Block[]) {
	const content = blocks.map(b =>
		b.type === "thinking"
			? { type: "thinking", thinking: b.thinking, thinkingSignature: "c2lnbmF0dXJl" }
			: b.type === "toolCall"
				? { type: "toolCall", id: `toolu_${seq + 1}`, name: b.name, arguments: b.arguments, intent: "Doing the thing" }
				: b,
	);
	return entry({
		type: "message",
		message: {
			role: "assistant",
			content,
			api: "anthropic-messages",
			provider: "anthropic",
			model: "claude-example",
			usage: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 15, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
			stopReason: "toolUse",
			timestamp: seq,
		},
	});
}

export const toolResult = (toolName: string, text: string, isError = false) =>
	entry({ type: "message", message: { role: "toolResult", toolCallId: `toolu_${seq}`, toolName, content: [{ type: "text", text }], details: {}, isError, timestamp: seq } });

export const bashExecution = (command: string, output: string) =>
	entry({ type: "message", message: { role: "bashExecution", command, output, exitCode: 0, cancelled: false, truncated: false, timestamp: seq, excludeFromContext: false } });

/** A harness notice; its content is a string array in real files. */
export const developer = (text: string) =>
	entry({ type: "message", message: { role: "developer", content: [`<system-reminder>\n${text}\n</system-reminder>`], attribution: "agent", timestamp: seq } });

/** A Dispatch answer as pi-envoy delivers it. Real files carry display: true; display: false must render the same. */
export const envoyMessage = (body: string, display = true) =>
	entry({ type: "custom_message", customType: "envoy-message", content: `envoy:\n  to: you (0000…)\n  from: dispatch\n  at: "2026-09-30T00:00:00Z"\n  id: example-1\n---\n${body}`, display, attribution: "agent" });

export const advisorCard = (note: string) =>
	entry({ type: "custom_message", customType: "advisor", content: `<advisory advisor="Example" guidance="weigh, don't blindly obey">\n${note}\n</advisory>`, display: true, details: { notes: [{ note }] }, attribution: "agent" });

export const hiddenCustomMessage = (customType: string, text: string) =>
	entry({ type: "custom_message", customType, content: text, display: false, attribution: "agent" });

export const toolExecutionStart = (toolName: string) =>
	entry({ type: "custom", customType: "tool_execution_start", data: { toolCallId: `toolu_${seq}`, toolName, startedAt: at(), args: { path: "/work/example/file.txt" }, intent: "Reading a file" } });

export const compaction = (summary: string, firstKeptEntryId: string) =>
	entry({
		type: "compaction",
		summary,
		shortSummary: "Archived earlier turns",
		firstKeptEntryId,
		tokensBefore: 800_000,
		tokensAfter: 270_000,
		method: "snapcompact",
		details: { modifiedFiles: [], readFiles: [] },
		fromExtension: false,
		preserveData: {},
	});

export const resetBoundary = () => entry({ type: "reset_boundary" });
export const unknownEntry = () => entry({ type: "some_future_entry", payload: { anything: true } });

/**
 * A branch of about 1 MiB: header entries, then turns of an assistant read call, its
 * tool_execution_start record and a 6 KiB result, then the newest turns that the window test reads.
 */
export function bigBranch() {
	const head = [titleSlot(), sessionHeader(), modelChange()];
	const turns: FixtureEntry[] = [user("Start the example task.")];
	for (let i = 0; turns.length < 600; i++) {
		turns.push(assistant({ type: "thinking", thinking: `Considering step ${i}.` }, { type: "toolCall", name: "read", arguments: { path: `/work/example/file-${i}.txt` } }));
		turns.push(toolExecutionStart("read"));
		turns.push(toolResult("read", `line of file ${i}\n`.repeat(400)));
	}
	const newest = [
		envoyMessage("Tester here: proceed with the comment, visible.", true),
		envoyMessage("Tester here: a hidden answer that must still reach the gate.", false),
		advisorCard(`An earlier advisory note that is long enough to be cut. ${"x".repeat(300)}`),
		developer("You stopped with 2 incomplete todo items."),
		assistant({ type: "text", text: "NEWEST MESSAGE: posting the comment now." }, { type: "toolCall", name: "write", arguments: { path: "xd://dispatch_comment", content: '{"issue":"X-1","body":"b"}' } }),
	];
	return [...head, ...turns, ...newest];
}
