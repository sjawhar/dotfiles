// Export the OMP session id into the process environment so child shells
// (bash tool, PTY) can key per-session state — e.g. pr-inbox's seen-state.
// Subagents load no extensions and share the parent process, so a session
// family deliberately shares one OMP_SESSION_ID (one set of eyes). Session
// switch/branch in the same process re-points the id. If per-session state
// ever grows beyond this (review-inbox ledgers, envoy-delivered read state),
// that belongs in an envoy-backed plugin, not more env vars.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	const set = (_event: unknown, ctx: { sessionManager?: { getSessionId?: () => string } }) => {
		const id = ctx.sessionManager?.getSessionId?.();
		if (id) process.env.OMP_SESSION_ID = id;
	};
	pi.on("session_start", set);
	pi.on("session_switch", set);
	pi.on("session_branch", set);
}
