import { describe, expect, test } from "bun:test";
import feature, { appendToTail, contextLine } from "../experiments/context-line";

type Pi = Parameters<typeof feature>[0];
type Msg = { role: string; content: unknown; timestamp: number };

const toolResult = (text: string): Msg => ({ role: "toolResult", content: [{ type: "text", text }], timestamp: 1 });
const user = (text: string): Msg => ({ role: "user", content: text, timestamp: 1 });
const assistant = (text: string): Msg => ({ role: "assistant", content: [{ type: "text", text }], timestamp: 1 });

/** The feature bound to a fake `pi`; `context` runs its hook with the usage `getContextUsage` reports. */
function bind(usage: { tokens: number; contextWindow: number } | undefined) {
	let handler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	feature({ on: (_event: string, h: typeof handler) => (handler = h) } as unknown as Pi);
	return (messages: Msg[]) => handler?.({ type: "context", messages }, { getContextUsage: () => usage }) as { messages?: Msg[] } | undefined;
}
/** The appended line as the model reads it: the whole string of a string tail, or the last block without its blank-line lead. */
const lastText = (r: { messages?: Msg[] } | undefined) => {
	const last = r?.messages?.at(-1);
	if (!last) return undefined;
	return typeof last.content === "string" ? last.content : (last.content as Array<{ text?: string }>).at(-1)?.text?.replace(/^\n\n/, "");
};

describe("appendToTail", () => {
	test("appends a text block, set off by a blank line, to a final toolResult and leaves every other message the same object", () => {
		const messages = [user("do it"), assistant("ok"), toolResult("output")];
		const out = appendToTail(messages, "[Token usage: 1/2]")!;
		expect(out).toHaveLength(3);
		expect(out[0]).toBe(messages[0]);
		expect(out[1]).toBe(messages[1]);
		expect(out[2]).not.toBe(messages[2]);
		// Adjacent text blocks reach the model run together, so the block carries the same blank line a string tail gets.
		expect((out[2].content as Array<{ text: string }>).map(b => b.text)).toEqual(["output", "\n\n[Token usage: 1/2]"]);
		expect((messages[2].content as unknown[]).length).toBe(1);
	});
	test("appends to a final string user message with a blank line", () => {
		expect(lastText({ messages: appendToTail([user("hi")], "[x]") })).toBe("hi\n\n[x]");
	});
	test("appends to a final custom or hook message, whose content reaches the model as a user message", () => {
		const custom = (content: unknown): Msg => ({ role: "custom", content, timestamp: 1 });
		expect(lastText({ messages: appendToTail([custom("envoy says hi")], "[x]") })).toBe("envoy says hi\n\n[x]");
		const out = appendToTail([custom([{ type: "text", text: "async result" }])], "[x]")!;
		expect((out[0].content as Array<{ text: string }>).map(b => b.text)).toEqual(["async result", "\n\n[x]"]);
		expect(lastText({ messages: appendToTail([{ role: "hookMessage", content: "hook", timestamp: 1 }], "[x]") })).toBe("hook\n\n[x]");
	});
	test("returns undefined when the final message is an assistant message or the list is empty", () => {
		expect(appendToTail([user("a"), assistant("b")], "[x]")).toBeUndefined();
		expect(appendToTail([], "[x]")).toBeUndefined();
	});
});

describe("the context hook", () => {
	test("puts the used tokens and the model's window, en-US separated, on the request's last message without mutating its input", () => {
		const context = bind({ tokens: 14_203, contextWindow: 1_000_000 });
		const messages = [user("do"), assistant("ok"), toolResult("out")];
		const snapshot = JSON.stringify(messages);
		expect(lastText(context(messages))).toBe("[Token usage: 14,203/1,000,000]");
		expect(JSON.stringify(messages)).toBe(snapshot);
		expect(contextLine(14_203, 32_768)).toBe("[Token usage: 14,203/32,768]");
	});
	test("leaves the request alone when usage is unknown or the window is zero", () => {
		expect(bind(undefined)([toolResult("out")])).toBeUndefined();
		expect(bind({ tokens: 10, contextWindow: 0 })([toolResult("out")])).toBeUndefined();
	});
});
