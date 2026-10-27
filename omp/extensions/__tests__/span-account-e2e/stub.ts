// span-account e2e harness (run.sh): a stub Anthropic Messages endpoint that reports an
// `anthropic-organization-id`, and an OTLP/HTTP trace sink. Usage: bun stub.ts <outDir>
import * as path from "node:path";

const outDir = process.argv[2] ?? ".";
const ORG = "5fd73c3e-e2e0-4000-8000-00000000e2e0";
let traces = 0;
const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

Bun.serve({
	port: 18080,
	hostname: "127.0.0.1",
	async fetch(req) {
		const url = new URL(req.url);
		const auth = req.headers.get("authorization") ?? req.headers.get("x-api-key") ?? "";
		await Bun.write(path.join(outDir, `req-${Date.now()}-${url.pathname.replaceAll("/", "_")}.txt`), `${req.method} ${url.pathname} auth=${auth.slice(0, 18)}\n`);
		if (!url.pathname.endsWith("/messages")) return Response.json({});
		const body =
			sse("message_start", { type: "message_start", message: { id: "msg_e2e", type: "message", role: "assistant", model: "claude-opus-5-5", content: [], stop_reason: null, usage: { input_tokens: 10, output_tokens: 1 } } }) +
			sse("content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }) +
			sse("content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "ok" } }) +
			sse("content_block_stop", { type: "content_block_stop", index: 0 }) +
			sse("message_delta", { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: 2 } }) +
			sse("message_stop", { type: "message_stop" });
		return new Response(body, { headers: { "content-type": "text/event-stream", "anthropic-organization-id": ORG, "request-id": "req_e2e" } });
	},
});

Bun.serve({
	port: 14318,
	hostname: "127.0.0.1",
	async fetch(req) {
		const url = new URL(req.url);
		const bytes = new Uint8Array(await req.arrayBuffer());
		if (url.pathname === "/v1/traces") await Bun.write(path.join(outDir, `traces-${++traces}.bin`), bytes);
		return new Response(new Uint8Array(0), { headers: { "content-type": "application/x-protobuf" } });
	},
});
console.log("stub ready");
