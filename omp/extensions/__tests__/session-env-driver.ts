// Runs session-env's session_start handler in a fresh process, so the module's
// import-time view of JJ_CONFIG is the one the test chose, and prints what the
// session exports. argv[2]: JSON `{ kind, file, hasUI, starts }`; `starts` (default
// 1) is how many session_start events the one process gets.
import sessionEnv from "../session-env";

type Handler = (event: unknown, ctx: unknown) => Promise<void>;
const { kind, file, hasUI, starts } = JSON.parse(process.argv[2] ?? "{}") as {
	kind: "main" | "sub";
	file?: string;
	hasUI?: boolean;
	starts?: number;
};
const handlers = new Map<string, Handler>();
sessionEnv({ on: (event: string, handler: Handler) => handlers.set(event, handler) } as never);
const notes: string[] = [];
let stderr = "";
const write = process.stderr.write.bind(process.stderr);
process.stderr.write = ((chunk: string | Uint8Array) => {
	stderr += String(chunk);
	return true;
}) as typeof process.stderr.write;
for (let i = 0; i < (starts ?? 1); i++) {
	await handlers.get("session_start")?.(undefined, {
		agent: { kind },
		hasUI: hasUI ?? false,
		ui: { notify: (message: string) => notes.push(message) },
		sessionManager: { getSessionFile: () => file },
	});
}
process.stderr.write = write;
process.stdout.write(
	JSON.stringify({
		id: process.env.OMP_SESSION_ID ?? null,
		trailers: process.env.PI_COMMIT_TRAILERS ?? null,
		jjConfig: process.env.JJ_CONFIG ?? null,
		said: [...notes, stderr].join("\n"),
	}),
);
