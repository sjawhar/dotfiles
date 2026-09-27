// Put this session on the attention queue when it is ready for Sami, and take
// it off when he has dealt with it. The queue and every tmux call live in
// scripts/tmux-attention; this file only maps omp events to push and drop.
//
// Ready: a turn ends and the session is idle (agent_end that is not an
// automatic continuation, not aborted, with no follow-up already queued), or
// the ask tool opens its dialog. Dealt with: Sami types into the session
// (any keystroke, wherever the pane is), the ask dialog closes, or the
// session exits. Nothing rings: completion.notify, ask.notify and error.notify
// are off in omp/config.yml, and this is what replaces them.
//
// Typing is read from the raw terminal input stream (ctx.ui.onTerminalInput),
// not the `input` event: that one fires only when a prompt is submitted, and
// the first keystroke is when he has arrived. session_start fires once per
// process; /new, /resume and fork clear every extension's terminal-input
// listener and then emit session_switch (session_branch for a branch), so the
// listener is registered on all three, the previous one unsubscribed first.
// ProcessTerminal and the TUI swallow the replies to their own queries
// (OSC 11, DA1, DECRPM, cursor position) before listeners see the data; the
// ones that still reach a listener are filtered here so a swap into a
// different-sized cell never counts as typing.
//
// Runs unchanged in an agent box: TMUX and TMUX_PANE are passed in and
// ~/.dotfiles and ~/.omp are mounted. Subagent sessions are skipped — only the
// top-level session has a pane Sami answers.
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

// TMUX_ATTENTION_SCRIPT exists so a workspace copy of the script can be smoked before it lands in ~/.dotfiles.
const SCRIPT = process.env.TMUX_ATTENTION_SCRIPT ?? path.join(os.homedir(), ".dotfiles", "scripts", "tmux-attention");
const PANE = process.env.TMUX_PANE;
// TMUX is "<socket>,<server pid>,<session index>". A pane id is only meaningful
// on the server that issued it, so the line carries the server pid and the
// script ignores lines from a server that is gone.
const SERVER = process.env.TMUX?.split(",")[1];
// Terminal-to-host reports, not keystrokes: cell size (CSI 6;h;w t), cursor
// position (CSI r;c R), focus in/out (CSI I / CSI O), SGR mouse (CSI < … M/m).
const TERMINAL_REPLY = /^(?:\x1b\[(?:6;\d+;\d+t|\d+;\d+R|[IO]|<[\d;]*[Mm]))+$/;

type Ctx = {
	agent: { isSubagent: boolean };
	cwd: string;
	hasPendingMessages: () => boolean;
	sessionManager: { getSessionId: () => string; getSessionName: () => string | undefined };
	ui: { notify: (message: string, type?: "info" | "warning" | "error") => void; onTerminalInput: (handler: (data: string) => undefined) => () => void };
};
type Assistant = { role: string; stopReason?: string };

export default function (pi: ExtensionAPI) {
	if (!PANE || !SERVER || !/^\d+$/.test(SERVER)) return;

	// True between a push and the drop that answers it, so a prompt being typed
	// costs one drop, not one per keystroke. The cockpit's `next` also removes
	// the line; the extra drop that follows is a no-op.
	let queued = false;
	let unsubscribe: (() => void) | undefined;

	// Every handler but session_shutdown fires and forgets: the tool loop never
	// waits on tmux. A failing push or drop is reported in the TUI, never on
	// stderr, which the TUI owns. exec resolves on non-zero exit rather than
	// rejecting.
	const run = (ctx: Ctx, args: string[]): Promise<void> =>
		pi.exec(SCRIPT, args).then(
			(result) => {
				if (result.code !== 0) {
					ctx.ui.notify(`attention-queue: tmux-attention ${args[0]} failed (${result.code}): ${result.stderr.trim()}`, "error");
				}
			},
			(err: unknown) => {
				ctx.ui.notify(`attention-queue: tmux-attention ${args[0]} failed: ${String(err)}`, "error");
			},
		);
	const push = (ctx: Ctx): void => {
		const line = {
			pane: PANE,
			server: SERVER,
			session: ctx.sessionManager.getSessionId(),
			title: ctx.sessionManager.getSessionName() || path.basename(ctx.cwd),
			cwd: ctx.cwd,
			at: new Date().toISOString(),
		};
		queued = true;
		void run(ctx, ["push", JSON.stringify(line)]);
	};
	const drop = (ctx: Ctx): Promise<void> => {
		queued = false;
		return run(ctx, ["drop", PANE]);
	};

	const listen = (_event: unknown, ctx: Ctx): void => {
		if (ctx.agent.isSubagent) return;
		unsubscribe?.();
		unsubscribe = ctx.ui.onTerminalInput((data) => {
			if (queued && !TERMINAL_REPLY.test(data)) void drop(ctx);
			return undefined;
		});
	};
	pi.on("session_start", listen);
	pi.on("session_switch", listen);
	pi.on("session_branch", listen);
	pi.on("agent_end", (event: { messages: Assistant[]; willContinue?: boolean }, ctx: Ctx) => {
		if (ctx.agent.isSubagent || event.willContinue) return;
		const last = event.messages.findLast((m) => m.role === "assistant");
		if (last?.stopReason === "aborted") return;
		if (ctx.hasPendingMessages()) return;
		push(ctx);
	});
	pi.on("tool_execution_start", (event: { toolName: string }, ctx: Ctx) => {
		if (ctx.agent.isSubagent || event.toolName !== "ask") return;
		push(ctx);
	});
	pi.on("tool_execution_end", (event: { toolName: string }, ctx: Ctx) => {
		if (ctx.agent.isSubagent || event.toolName !== "ask") return;
		void drop(ctx);
	});
	// Awaited: the line must be gone before the process is, or a later `next`
	// swaps whatever now runs in this pane into the cockpit.
	pi.on("session_shutdown", (_event: unknown, ctx: Ctx) => {
		if (ctx.agent.isSubagent) return;
		return drop(ctx);
	});
}
