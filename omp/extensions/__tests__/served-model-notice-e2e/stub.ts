// served-model-notice e2e harness (run.sh): a stub Anthropic Messages endpoint. No network, no
// real key. Every request body is written to STUB_OUT as req-<n>-<who>-<model>[-429].json.
// Env: STUB_PORT, STUB_OUT, STUB_SCENARIO:
//   none    - every model answers.
//   chain   - claude-opus-5-5 answers 429; claude-opus-5 answers.
//   deep    - 5-5, 5 and 4-8 answer 429; claude-opus-4-7 answers (a drop below the floor).
//   inreq   - claude-fable-5-1's first answer carries an Anthropic `fallback` block to claude-opus-5.
//   compact - like chain, but the worker's first good answer reports 195k input tokens, so a
//             session with a 100k compaction threshold compacts; the summary request gets a fixed
//             summary.
//   restore - claude-opus-5-5 answers 429 until claude-opus-5 has answered once, so
//             refusal-cooldown.ts (with a 1 ms cooldown) can switch the session back.
// The worker is the session whose first user message holds SUBMARKER (a task subagent) or
// TOPMARKER (a top-level session run directly); it reads a file twice, then answers. The parent
// (no marker) spawns one SUBMARKER task and waits for it.
import * as path from "node:path";

const PORT = Number(process.env.STUB_PORT ?? 18741);
const OUT = process.env.STUB_OUT ?? ".";
const SCENARIO = process.env.STUB_SCENARIO ?? "none";
let seq = 0;

const FAIL: Record<string, string[]> = {
	none: [],
	chain: ["claude-opus-5-5"],
	compact: ["claude-opus-5-5"],
	deep: ["claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"],
	inreq: [],
	// restore: 5-5 fails until opus-5 has served once, so refusal-cooldown's restore succeeds.
	restore: ["claude-opus-5-5"],
};
let opus5Served = false;

type Block = { type: string; text?: string; content?: unknown };
type Msg = { role: string; content: string | Block[] };

function textOf(value: unknown): string {
	if (typeof value === "string") return value;
	if (Array.isArray(value)) return value.map(textOf).join("\n");
	if (value && typeof value === "object") {
		const v = value as Record<string, unknown>;
		return [v.text, v.content].map(textOf).join("\n");
	}
	return "";
}

const sse = (events: Array<[string, unknown]>) =>
	new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""), {
		headers: { "content-type": "text/event-stream", "request-id": `req_${seq}` },
	});

function reply(model: string, blocks: Array<Record<string, unknown>>, opts: { input?: number; stop?: string } = {}) {
	const events: Array<[string, unknown]> = [
		["message_start", { type: "message_start", message: { id: `msg_${seq}`, type: "message", role: "assistant", model, content: [], stop_reason: null, usage: { input_tokens: opts.input ?? 1000, output_tokens: 1 } } }],
	];
	blocks.forEach((block, index) => {
		if (block.type === "text") {
			events.push(["content_block_start", { type: "content_block_start", index, content_block: { type: "text", text: "" } }]);
			events.push(["content_block_delta", { type: "content_block_delta", index, delta: { type: "text_delta", text: block.text } }]);
		} else if (block.type === "tool_use") {
			events.push(["content_block_start", { type: "content_block_start", index, content_block: { type: "tool_use", id: block.id, name: block.name, input: {} } }]);
			events.push(["content_block_delta", { type: "content_block_delta", index, delta: { type: "input_json_delta", partial_json: JSON.stringify(block.input) } }]);
		} else {
			events.push(["content_block_start", { type: "content_block_start", index, content_block: block }]);
		}
		events.push(["content_block_stop", { type: "content_block_stop", index }]);
	});
	events.push(["message_delta", { type: "message_delta", delta: { stop_reason: opts.stop ?? "end_turn" }, usage: { output_tokens: 5 } }]);
	events.push(["message_stop", { type: "message_stop" }]);
	return sse(events);
}

const rateLimited = () =>
	Response.json(
		{ type: "error", error: { type: "rate_limit_error", message: "This request would exceed your organization's rate limit (stub)." } },
		{ status: 429, headers: { "retry-after": "1" } },
	);

Bun.serve({
	port: PORT,
	hostname: "127.0.0.1",
	idleTimeout: 120,
	async fetch(req) {
		const url = new URL(req.url);
		if (!url.pathname.endsWith("/messages")) return Response.json({ data: [] });
		const body = (await req.json()) as { model: string; system?: unknown; messages: Msg[]; tools?: Array<{ name: string }> };
		const n = ++seq;
		// User text only (not tool results, where the parent sees its worker's output): a notice may come before the assignment.
		const all = body.messages
			.filter(m => m.role === "user")
			.map(m => (typeof m.content === "string" ? m.content : m.content.filter(b => b.type === "text").map(b => b.text).join("\n")))
			.join("\n");
		const marker = /SUBMARKER/.test(textOf(body.messages)) ? "SUBMARKER" : "TOPMARKER";
		const sys = textOf(body.system);
		const isSummary = /summar/i.test(sys.slice(0, 4000)) && !/TOPMARKER|SUBMARKER/.test(sys) && !body.tools?.length;
		const worker = /SUBMARKER|TOPMARKER/.test(all) && !isSummary;
		const role = isSummary ? "summary" : worker ? (all.includes("SUBMARKER") ? "sub" : "top") : "parent";
		const failing = (FAIL[SCENARIO] ?? []).includes(body.model) && !(SCENARIO === "restore" && opus5Served);
		if (worker && body.model === "claude-opus-5") opus5Served = true;
		await Bun.write(path.join(OUT, `req-${String(n).padStart(3, "0")}-${role}-${body.model}${failing && worker ? "-429" : ""}.json`), JSON.stringify(body, null, 1));

		if (isSummary) return reply(body.model, [{ type: "text", text: `## Summary\nThe worker was asked to run ${marker} work and has read a file.` }]);

		const toolResults = body.messages.filter(m => Array.isArray(m.content) && m.content.some(b => b.type === "tool_result")).length;
		if (!worker) {
			if (toolResults === 0) {
				return reply(body.model, [{ type: "tool_use", id: `toolu_task_${n}`, name: "task", input: { context: "probe", tasks: [{ name: "Probe", task: "SUBMARKER read /etc/hostname, then say which model you are.", solutionSpace: "one fix: probe" }] } }], { stop: "tool_use" });
			}
			if (toolResults === 1) return reply(body.model, [{ type: "tool_use", id: `toolu_wait_${n}`, name: "wait", input: {} }], { stop: "tool_use" });
			return reply(body.model, [{ type: "text", text: "PARENT DONE" }]);
		}
		if (failing) return rateLimited();
		const blocks: Array<Record<string, unknown>> = [];
		if (SCENARIO === "inreq" && body.model === "claude-fable-5-1" && toolResults === 0) {
			blocks.push({ type: "fallback", from: { model: "claude-fable-5-1" }, to: { model: "claude-opus-5" } });
		}
		if (toolResults < 2) {
			blocks.push({ type: "text", text: `step ${toolResults}` });
			blocks.push({ type: "tool_use", id: `toolu_read_${n}`, name: "read", input: { path: "/etc/hostname" } });
			const input = SCENARIO === "compact" && toolResults === 0 ? 195_000 : 1000;
			return reply(body.model, blocks, { stop: "tool_use", input });
		}
		blocks.push({ type: "text", text: "WORKER DONE" });
		return reply(body.model, blocks);
	},
});
console.log(`stub ${SCENARIO} on ${PORT}`);
