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
 * Snapshots run fire-and-forget — never adds latency to the tool loop, never
 * breaks a tool call on failure. A failure is still logged: a snapshot that
 * stops recording is exactly the thing this net exists to prevent, and
 * silence there looks identical to working.
 *
 * At most one snapshot runs per workspace root across every omp process and
 * every session in one: each runs under `flock --nonblock` on
 * `<root>/.jj/omp-jj-snapshot.lock`, and a call that finds it held skips (the
 * next tool call catches its change). The in-process `inflight` set only
 * saves spawning flock to be refused. Each subagent loads its own copy of this
 * hook, so before the flock one process ran a snapshot per subagent: 15 at a
 * time on one root, queued on jj's repository-wide `git_import_export.lock`.
 *
 * A snapshot is never killed. Every commit a snapshot writes updates a
 * `refs/jj/keep/*` ref, for which gix holds `.git/packed-refs.lock` (an
 * O_EXCL file, not a kernel lock) whenever `packed-refs` exists; a kill
 * inside that window leaves the 0-byte lock behind, and every later git or jj
 * ref update in the repository fails with "Could not lock packed refs" until
 * someone deletes it. SIGKILL there always leaves it, and SIGTERM sometimes
 * does: gix's signal handler skips a lock file whose registry slot is busy
 * (gix-tempfile `registry.rs`); a SIGTERM mid-snapshot can also leave the
 * working copy stale. `pi.exec`'s `timeout` SIGKILLs at once, which
 * is why the hook spawns jj itself. A snapshot still running after SLOW_MS is
 * logged once and left to finish; until it does, this root's snapshots skip.
 */
import * as path from "node:path";
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

const MUTATING_TOOLS: Record<string, true> = {
	edit: true,
	write: true,
	bash: true,
	eval: true,
	ast_edit: true,
	lsp: true,
};

/** A snapshot still running after this is logged (and left running). */
const SLOW_MS = 120_000;
/** flock's exit code when another snapshot of the root holds the lock. */
const LOCK_BUSY = 75;
/** util-linux flock; without it (macOS) only the in-process guard applies. */
const FLOCK = Bun.which("flock");

export interface SnapshotResult {
	/** Another snapshot of this root was running; nothing ran. */
	busy: boolean;
	code: number | null;
	stderr: string;
}

/**
 * Run `jj util snapshot` in `root` unless another one holds the root's lock.
 * `onSlow` runs once if it is still running after `slowMs`; nothing stops it.
 */
export async function snapshot(
	root: string,
	opts: { slowMs?: number; onSlow?: () => void } = {},
): Promise<SnapshotResult> {
	const { slowMs = SLOW_MS, onSlow } = opts;
	const jj = ["jj", "util", "snapshot"];
	// --no-fork: flock takes the lock and execs jj (the jj shim execs jj in
	// the same pid), so the lock lives exactly as long as jj does.
	const argv = FLOCK
		? [
				FLOCK,
				"--nonblock",
				"--no-fork",
				"--conflict-exit-code",
				String(LOCK_BUSY),
				path.join(root, ".jj", "omp-jj-snapshot.lock"),
				...jj,
			]
		: jj;
	// detached: its own session and process group, so a Ctrl-C or hangup
	// aimed at omp's group does not reach jj mid-write either.
	const proc = Bun.spawn(argv, { cwd: root, detached: true, stdin: "ignore", stdout: "ignore", stderr: "pipe" });
	const slowTimer = onSlow ? setTimeout(onSlow, slowMs) : undefined;
	try {
		const [stderr, code] = await Promise.all([new Response(proc.stderr).text(), proc.exited]);
		return { busy: Boolean(FLOCK) && code === LOCK_BUSY, code, stderr: stderr.trim() };
	} finally {
		clearTimeout(slowTimer);
	}
}

export default function jjSnapshot(pi: HookAPI): void {
	/** cwd -> jj workspace root, or null when cwd is not inside a jj repo. */
	const roots = new Map<string, string | null>();
	/** Roots with a snapshot from this hook instance running. */
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
		void snapshot(root, {
			onSlow: () =>
				pi.logger.warn("jj-snapshot: 'jj util snapshot' is still running; this root's snapshots skip until it ends", {
					root,
					slowMs: SLOW_MS,
				}),
		})
			.then((res) => {
				if (res.busy || res.code === 0) return;
				pi.logger.warn("jj-snapshot: 'jj util snapshot' failed; this working copy is not being recorded", {
					root,
					code: res.code,
					stderr: res.stderr,
				});
			})
			.catch((err: unknown) => {
				pi.logger.warn("jj-snapshot: could not run 'jj util snapshot'", { root, err: String(err) });
			})
			.finally(() => {
				inflight.delete(root);
			});
	});
}
