/**
 * jj-snapshot — permanent time-travel safety net.
 *
 * After every mutating tool call (edit/write/bash/eval/ast_edit/lsp), force a
 * jj working-copy snapshot so the operation log records the state after each
 * agent action. Recovery is file-level and op-store-safe:
 *
 *   jj op log                          # find the moment
 *   jj diff --at-op <op>               # inspect what changed
 *   jj restore --from <commit>         # restore files (never `jj op restore`
 *                                      #   in shared repos — it discards other
 *                                      #   agents' operations)
 *
 * `jj util snapshot` is self-debouncing: an unchanged working copy records
 * no new operation, so per-tool-call frequency costs nothing when idle.
 * Snapshots run fire-and-forget with an in-flight guard per repo root —
 * never adds latency to the tool loop, never breaks a tool call on failure.
 */
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

const MUTATING_TOOLS: Record<string, true> = {
	edit: true,
	write: true,
	bash: true,
	eval: true,
	ast_edit: true,
	lsp: true,
};

export default function jjSnapshot(pi: HookAPI): void {
	/** cwd -> jj workspace root, or null when cwd is not inside a jj repo. */
	const roots = new Map<string, string | null>();
	/** Repo roots with a snapshot currently running (jj takes a repo lock). */
	const inflight = new Set<string>();

	async function jjRoot(cwd: string): Promise<string | null> {
		const cached = roots.get(cwd);
		if (cached !== undefined) return cached;
		// --ignore-working-copy: the root lookup itself must not snapshot or lock.
		const res = await pi.exec("jj", ["root", "--ignore-working-copy"], {
			cwd,
			timeout: 5_000,
		});
		const root = res.code === 0 ? res.stdout.trim() : null;
		roots.set(cwd, root);
		return root;
	}

	pi.on("tool_result", async (event, ctx) => {
		if (!MUTATING_TOOLS[event.toolName]) return;
		const root = await jjRoot(ctx.cwd);
		if (!root || inflight.has(root)) return;
		inflight.add(root);
		// Fire-and-forget: a missed snapshot is caught by the next tool call.
		void pi
			.exec("jj", ["util", "snapshot"], { cwd: root, timeout: 30_000 })
			.catch(() => {})
			.finally(() => {
				inflight.delete(root);
			});
	});
}
