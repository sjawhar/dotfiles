// Refuse an `edit`, `write` or `ast_edit` call that would rewrite a shell script a shell is running.
//
// omp's file tools rewrite a file in place, on the same inode. bash (and dash, busybox sh, zsh,
// ksh) does not load a script into memory: it keeps the file open and reads it by byte offset,
// re-reading after each command. Rewritten under it, the running copy resumes at the old offset in
// the new bytes and runs half-lines or different commands. Two long jobs were lost this way on
// 2026-10-24 and 2026-10-25 (dispatch://INFRA-435).
//
// Before such a call, this resolves each target (against the session cwd, through symlinks) and
// looks for a shell process of the current user holding the same device and inode open on a
// read-only, close-on-exec fd of 10 or above. That is how a shell holds the script it runs: bash on
// fd 255, dash and busybox sh on fd 10, all O_RDONLY|O_CLOEXEC. fd 10 and above alone is not
// enough: bash's named fds (`exec {lk}>lock`) live there too, and a user's `exec 10<notes.txt` is
// read-only but not close-on-exec; neither blocks. One false positive is left: a shell that
// redirects an fd already open on a read-only file (stdin `< data.txt`, then `{ …; } < other`)
// saves the original on fd 10 with the same O_RDONLY|O_CLOEXEC flags as a script fd, so an edit to
// data.txt is refused while that redirect lasts. A saved writable fd (a log) does not block. bash's
// `source` reads its file whole and keeps nothing open, so it is safe and not
// blocked; dash's and busybox's `.` keep the file on fd 10 like a script, and are blocked. A `tail
// -f`, an editor or a Python process holding the file is not a shell, so it never blocks. On a
// match the call is refused with the file, the pid, its command line and what to do instead.
//
// Edit targets come from omp's own parser (`editInspect` in pi-natives), so every edit mode's
// headers count, multi-file hashline and apply_patch input included. ast_edit's `paths` may name a
// directory or a glob: a running script under the directory or matching the glob counts.
//
// It fails open — the call runs and one debug line goes to omp's log — when /proc is missing
// (macOS), when a payload does not parse, on any exception, and when the whole check (the /proc
// walk and the target stats) takes longer than WALK_DEADLINE_MS (2 s; OMP_RUNNING_SCRIPT_GUARD_MS
// overrides it). That bound matters: omp refuses a call whose tool_call handler outlives
// `extensionHandlers.toolCallTimeoutMs`, and a /proc read of a process stuck in disk wait can hang.
// A process that cannot be read is skipped. The walk reads a process's `exe` link first and its
// cmdline only when that names a shell, since a cmdline read takes the target's mmap lock.
import type { Stats } from "node:fs";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { editInspect } from "@oh-my-pi/pi-natives";

type CallEvent = { toolName: string; input: Record<string, unknown> };
type Ctx = { cwd: string };
type Holder = { pid: number; cmdline: string; dev: number; ino: number; file: string };

const SHELLS: Record<string, true> = { bash: true, sh: true, dash: true, zsh: true, ksh: true, mksh: true, ash: true, hush: true };
const MIN_SCRIPT_FD = 10;
const O_ACCMODE = 0o3;
const O_CLOEXEC = 0o2000000;
const GLOB_CHARS = /[*?[{]/;
const WALK_DEADLINE_MS = Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) > 0 ? Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) : 2_000;

/** Raw target strings a call names, by tool; path-guard.ts reads the same targets. */
export function targetsOf(toolName: string, input: Record<string, unknown>): string[] {
	if (toolName === "write") return typeof input.path === "string" ? [input.path] : [];
	if (toolName === "ast_edit") {
		const paths = input.paths;
		if (typeof paths === "string") return [paths];
		return Array.isArray(paths) ? paths.filter((p): p is string => typeof p === "string") : [];
	}
	if (toolName !== "edit") return [];
	// The tool_call event does not say which edit mode is active; the input's shape does, except
	// that three modes share `{ input: string }`, so those are inspected under each of them.
	const modes = typeof input.input === "string" ? ["hashline", "apply_patch", "sloppy"] : Array.isArray(input.edits) ? ["patch"] : ["replace"];
	const found = new Set<string>();
	for (const mode of modes) {
		try {
			for (const p of editInspect(mode, JSON.stringify(input)).paths) found.add(p);
		} catch {
			// This mode's grammar does not fit the payload.
		}
	}
	return [...found];
}

function resolveTarget(raw: string, cwd: string): string | undefined {
	const trimmed = raw.trim();
	if (trimmed === "" || /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return undefined; // internal URLs
	const expanded = trimmed === "~" || trimmed.startsWith("~/") ? path.join(os.homedir(), trimmed.slice(1)) : trimmed;
	return path.resolve(cwd, expanded);
}

/** Whether a process is a shell, from its exe; busybox only when cmdline names a shell applet. */
async function isShell(proc: string): Promise<boolean> {
	const name = path.basename(await fs.readlink(`${proc}/exe`));
	if (name !== "busybox") return SHELLS[name] === true;
	const argv = (await fs.readFile(`${proc}/cmdline`, "utf8")).split("\0");
	const applet = path.basename(argv[0] ?? "");
	return SHELLS[applet === "busybox" ? (argv[1] ?? "") : applet] === true;
}

/** Every file the current user's shells (other than this process) hold open as a running script. */
async function shellHeldFiles(): Promise<Holder[]> {
	const uid = process.getuid?.();
	const holders: Holder[] = [];
	const pids = (await fs.readdir("/proc")).filter(name => /^\d+$/.test(name) && Number(name) !== process.pid);
	await Promise.all(
		pids.map(async pid => {
			try {
				const proc = `/proc/${pid}`;
				if ((await fs.stat(proc)).uid !== uid) return;
				// The exe link first: a cmdline read takes the process's mmap lock and can hang.
				if (!(await isShell(proc))) return;
				let cmdline: string | undefined;
				for (const fd of await fs.readdir(`${proc}/fd`)) {
					if (Number(fd) < MIN_SCRIPT_FD) continue;
					try {
						// A script fd is read-only and close-on-exec; a `{lk}>lock`, a saved stdout or a
						// user's `exec 10<file` is not.
						const flags = Number.parseInt(/^flags:\s+([0-7]+)$/m.exec(await fs.readFile(`${proc}/fdinfo/${fd}`, "utf8"))?.[1] ?? "", 8);
						if (Number.isNaN(flags) || (flags & O_ACCMODE) !== 0 || (flags & O_CLOEXEC) === 0) continue;
						const st = await fs.stat(`${proc}/fd/${fd}`);
						if (!st.isFile()) continue;
						const file = (await fs.readlink(`${proc}/fd/${fd}`)).replace(/ \(deleted\)$/, "");
						cmdline ??= (await fs.readFile(`${proc}/cmdline`, "utf8")).split("\0").filter(Boolean).join(" ");
						holders.push({ pid: Number(pid), cmdline, dev: st.dev, ino: st.ino, file });
					} catch {
						// The fd closed while we looked.
					}
				}
			} catch {
				// The process exited, or is not ours to read.
			}
		}),
	);
	return holders;
}

/** The holder whose file this target would rewrite, if any. */
async function matchTarget(target: string, holders: Holder[], globs: boolean): Promise<Holder | undefined> {
	// A target that does not exist is a new file, or (for ast_edit) a glob.
	const st: Stats | undefined = await fs.stat(target).catch(() => undefined);
	if (st?.isFile()) return holders.find(h => h.dev === st.dev && h.ino === st.ino);
	if (!globs) return undefined;
	if (st?.isDirectory()) {
		const dir = await fs.realpath(target);
		return holders.find(h => h.file.startsWith(`${dir}/`));
	}
	if (st === undefined && GLOB_CHARS.test(target)) {
		const glob = new Bun.Glob(target);
		return holders.find(h => glob.match(h.file));
	}
	return undefined;
}

function refusal(holder: Holder, toolName: string): string {
	return [
		`Running-script guard: nothing in this ${toolName} call ran. ${holder.file} is being run by pid ${holder.pid} (${holder.cmdline}).`,
		`${toolName} rewrites the file in place, and the running shell reads its script from that file by offset, so it would run shifted or garbled lines.`,
		`Wait for pid ${holder.pid} to finish, then send the call again; or write the new version to another name and \`mv\` it over ${path.basename(holder.file)} (a rename leaves the running copy reading the old file).`,
	].join("\n");
}

/** The running shell whose script one of these targets is, if any. */
async function findHolder(targets: string[], globs: boolean): Promise<Holder | undefined> {
	const holders = await shellHeldFiles();
	if (holders.length === 0) return undefined;
	for (const target of targets) {
		const holder = await matchTarget(target, holders, globs);
		if (holder) return holder;
	}
	return undefined;
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event: CallEvent, ctx: Ctx) => {
		if (event.toolName !== "edit" && event.toolName !== "write" && event.toolName !== "ast_edit") return undefined;
		const { promise: expired, resolve: expire } = Promise.withResolvers<"expired">();
		const timer = setTimeout(() => expire("expired"), WALK_DEADLINE_MS);
		try {
			const targets = targetsOf(event.toolName, event.input)
				.map(raw => resolveTarget(raw, ctx.cwd))
				.filter((t): t is string => t !== undefined);
			if (targets.length === 0) return undefined;
			// omp refuses the call if this handler outlives its deadline, and a /proc read can hang:
			// past WALK_DEADLINE_MS the check is abandoned and the call runs.
			const found = await Promise.race([findHolder(targets, event.toolName === "ast_edit"), expired]);
			if (found === "expired") {
				pi.logger.debug("running-script-guard: check took longer than its deadline; skipped", { tool: event.toolName, deadlineMs: WALK_DEADLINE_MS });
				return undefined;
			}
			if (found) return { block: true, reason: refusal(found, event.toolName) };
		} catch (error) {
			pi.logger.debug("running-script-guard: check skipped", { tool: event.toolName, error: error instanceof Error ? error.message : String(error) });
		} finally {
			clearTimeout(timer);
		}
		return undefined;
	});
}
