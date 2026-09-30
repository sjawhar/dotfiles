// Stands in for omp as a pane's own command: loads the extension with an exec that spawns each call
// in this process's group, as omp's pi.exec does, runs the session_shutdown handler, says so on
// stdout, and waits for the hangup that closes the pane. TMUX, TMUX_PANE, TMUX_ATTENTION_SCRIPT and
// OMP_ATTENTION_DIR come from the test.
import { spawn } from "node:child_process";
import extension from "../attention-queue";

type Pi = Parameters<typeof extension>[0];

const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
extension({
	on: (event: string, handler: (event: unknown, ctx: unknown) => unknown) => handlers.set(event, handler),
	exec: (command: string, args: string[]) => {
		const { promise, resolve } = Promise.withResolvers<{ code: number; stdout: string; stderr: string }>();
		spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] }).on("exit", (code) =>
			resolve({ code: code ?? 1, stdout: "", stderr: "" }),
		);
		return promise;
	},
} as unknown as Pi);
handlers.get("session_shutdown")?.({}, {
	agent: { kind: "main" },
	mode: "tui",
	hasUI: true,
	cwd: "/work/repo",
	hasPendingMessages: () => false,
	sessionManager: { getSessionId: () => "s", getSessionName: () => undefined },
	ui: { notify: () => undefined, onTerminalInput: () => () => undefined },
});
process.stdout.write("shut down\n");
setInterval(() => undefined, 1 << 30);
