// The remaining-context line: every request carries "[Token usage: <used>/<window>]" at the end
// of its last message, so the model can see how much of its context window is spent. It runs in
// top-level sessions and subagents alike, while the experiments extension's `context_line` gate is
// on (index.ts).
//
// The line rides the `context` hook, which hands a deep copy of the messages about to be sent and
// never touches history; a changed final message is marked per-call, so the Anthropic cache anchor
// moves one message back and only that message's bytes are re-billed.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

type TextBlock = { type: "text"; text: string };
type Block = TextBlock | { type: string };
type Msg = { role: string; content: string | Block[] };
type Ctx = { getContextUsage(): { tokens: number; contextWindow: number } | undefined };

export function contextLine(tokens: number, window: number): string {
	return `[Token usage: ${tokens.toLocaleString("en-US")}/${window.toLocaleString("en-US")}]`;
}

/**
 * Roles whose content reaches the model as user-side text. `custom` and `hookMessage` (Envoy
 * messages, async task results, IRC deliveries, reminders) convert to a user message carrying
 * their content, so skipping them would leave every request that follows one without the line.
 */
const TAIL_ROLES: Record<string, true> = { toolResult: true, user: true, developer: true, custom: true, hookMessage: true };

/**
 * A new array whose final message carries `text` after a blank line; undefined when there is
 * nothing to append to. Block content gets its own text block, and that block carries the blank
 * line too: the model reads adjacent text blocks run together, so a bare block reads as the end
 * of the tool's own last line.
 */
export function appendToTail<M extends Msg>(messages: M[], text: string): M[] | undefined {
	const last = messages[messages.length - 1];
	if (!last || !Object.hasOwn(TAIL_ROLES, last.role)) return undefined;
	let next: M;
	if (Array.isArray(last.content)) {
		next = { ...last, content: [...last.content, { type: "text", text: `\n\n${text}` }] };
	} else if (typeof last.content === "string") {
		next = { ...last, content: `${last.content}\n\n${text}` };
	} else {
		return undefined;
	}
	return [...messages.slice(0, -1), next];
}

export default function contextLineFeature(pi: ExtensionAPI): void {
	pi.on("context", (event: { messages: Msg[] }, ctx: Ctx) => {
		const usage = ctx.getContextUsage();
		if (!usage || usage.contextWindow <= 0) return undefined;
		const messages = appendToTail(event.messages, contextLine(usage.tokens, usage.contextWindow));
		return messages ? { messages } : undefined;
	});
}
