// Keep omp's own file tools from rewriting a script under the shell that is running it.
//
// This guard changes no file it finds a shell running, because it cannot tell which of them are
// ours. omp sets OMPCODE=1 for every process it starts, and every descendant inherits it: a test
// harness, the pytest under it, and the `bash driver.sh` that pytest starts all carry it. So "a
// script an agent's shell is running" and "a script that is ours to change" are different sets, and
// nothing in /proc separates them. On 2026-11-01 the guard acted on the first set and changed
// files of other programs (below).
//
// bash (and dash, busybox sh, zsh, ksh) does not load a script into memory: it keeps the file open
// and reads it by byte offset, re-reading after each command. Rewritten in place, on the same inode,
// the running copy resumes at the old offset in the new bytes and runs half-lines or different
// commands. Long jobs were lost this way on 2026-10-24 and 2026-10-25 (dispatch://INFRA-435), and on
// 2026-10-30 a `python3 - <<EOF ... open(p,'w')` in a bash call broke another session's test job
// (dispatch://INFRA-901). A rename does not do this: the running shell keeps reading the old inode.
//
// A running script is one a shell process of the current user holds open on a read-only,
// close-on-exec fd of 10 or above: bash holds its script on fd 255, dash and busybox sh on fd 10, all
// O_RDONLY|O_CLOEXEC. fd 10 and above alone is not enough: bash's named fds (`exec {lk}>lock`) live
// there too, and a user's `exec 10<notes.txt` is read-only but not close-on-exec; neither counts. One
// false positive is left: a shell that redirects an fd already open on a read-only file (stdin
// `< data.txt`, then `{ …; } < other`) saves the original on fd 10 with the same flags, so data.txt
// counts while that redirect lasts. bash's `source` reads its file whole and keeps nothing open;
// dash's and busybox's `.` keep the file on fd 10 like a script. A `tail -f`, an editor or a Python
// process holding a file is not a shell. The /proc walk reads a process's `exe` link first and its
// cmdline (and environ) only when that names a shell, since those reads take the target's mmap lock.
//
// One protection, Linux-only (no /proc, no protection): refusal. An `edit`, `write` or `ast_edit`
// call whose target a shell is running, by inode, is refused with the file, the pid, its command
// line and what to do instead. Edit targets come from omp's own parser (`editInspect` in
// pi-natives), so every edit mode's headers count; ast_edit's `paths` may name a directory or a
// glob, and a running script under it counts. The check fails open (the call runs, one debug line
// goes to omp's log) on any exception and when it takes longer than WALK_DEADLINE_MS (2 s;
// OMP_RUNNING_SCRIPT_GUARD_MS overrides it): omp refuses a call whose tool_call handler outlives
// `extensionHandlers.toolCallTimeoutMs`, and a /proc read can hang.
//
// The guard no longer protects the running copy itself. From 2026-10-31 to 2026-11-01 it did, against
// writers it cannot see (a bash or eval call), first by taking the script's write bits (005cb5b),
// then by renaming a copy over its path (ac7706c), choosing the scripts by walking /proc for every
// shell of this user whose environment carries OMPCODE=1. Passes were shared by every omp process of
// the user, so the process that acted was usually in no relation to the script. On 2026-11-01:
//   - another lane's test harness wrote driver.sh in a fresh mkdtemp directory and ran it; passes of
//     omp processes outside its process tree took its write bits (0644 -> 0444, the same inode), the
//     bits outlived the run, and the harness's next write of the path failed with PermissionError.
//     Measured from a third lane's scratch: 5 of 24 runs flipped, and 0 of 24 on a never-run decoy
//     with the same bytes beside it, so the selector, not the path, chose the file. A protection the
//     file carries outlives the run: a give-back driven by the shell's own exit through a pidfd still
//     left 32 of 50 rewrites refused, measured the same day.
//   - a pass put scripts/agent-session's bytes, mode and mtime at shims/omp, and every omp launch on
//     the box failed for ten minutes; later, shims/knives's bytes at shims/gh. Each shim ends with an
//     exec of the other script: the pass found bash holding one on fd 255, the shell exec'd, and the
//     copy read fd 255 after it. Taking the bits through the moved fd hit the other file, which no
//     record named. The copy also races any writer of the same file: an in-place writer that opened
//     the path before the rename writes into the running copy, and its new version is lost.
// What a shell runs is its owner's to protect; the refusal covers omp's own file tools.
//
// Passes now only give back write bits that those guards took. Each take wrote a record (the path,
// inode, original mode, the mode it left) under `omp-running-script-guard/pid-<pid namespace>` in
// $XDG_STATE_HOME or ~/.local/state. A pass gives the bits back to the file at the record's path
// only while it has exactly the mode the guard left and the record still stands: the same inode,
// running or not, or a copy that a rename writer made after the bits were gone (`sed -i` and five
// other rename writers copy the mode they find). Bits given to such a copy are the one change to a
// file the guard did not take them from, so each gets a warning in omp's log and a line in
// guard.log beside the records, as does each run of running-script-guard-restore. A later chmod by
// anyone else wins. Sessions still running an older guard keep taking bits until they restart or
// their pass lock is held from outside (their passes skip while `pass.lock` exists and is younger
// than a minute); a pass by this one gives back what they take. A take through an fd that had moved
// to another file (before 34cbf94) left no record for the file it hit, so nothing gives that back.
//
// Session start, every turn start and every tool call start a pass, at most one per
// PASS_INTERVAL_MS (2 s) across all omp processes of the user (a lock and a stamp in the state
// directory, shared with the older guards); a tool call waits for one (at most WALK_DEADLINE_MS)
// only when none finished within STALE_MS (5 s), and session end forces one.
// scripts/running-script-guard-restore gives every record back with no omp session. A call that
// fails on a recorded file gets lines appended that say why, what to do, and that command.
import type { Stats } from "node:fs";
import * as fsSync from "node:fs";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { editInspect } from "@oh-my-pi/pi-natives";

type CallEvent = { toolName: string; input: Record<string, unknown> };
type ResultEvent = { content: Array<{ type: string; text?: string }> };
type Ctx = { cwd: string };
export type Holder = { pid: number; fd: number; cmdline: string; dev: number; ino: number; file: string };
/** One script whose write bits an older guard took: `mode` is the mode before, `reduced` the mode it left, `at` when. */
type Entry = { path: string; dev: number; ino: number; mode: number; reduced: number; at: number; pid: number; cmdline: string };

export type GuardOptions = {
	/** Shared by every omp process of this user in this pid namespace: the records, the pass lock and stamp. */
	stateDir: string;
	passIntervalMs: number;
	/** How long a tool call waits for a pass it started: 0 in production; tests wait for the whole pass. */
	passWaitMs: number;
};

const SHELLS: Record<string, true> = { bash: true, sh: true, dash: true, zsh: true, ksh: true, mksh: true, ash: true, hush: true };
const MIN_SCRIPT_FD = 10;
const O_ACCMODE = 0o3;
const O_CLOEXEC = 0o2000000;
const MODE_BITS = 0o7777;
const GLOB_CHARS = /[*?[{]/;
const WALK_DEADLINE_MS = Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) > 0 ? Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) : 2_000;
const PASS_INTERVAL_MS = 2_000;
/** A pass running longer is abandoned, and a lock older than twice this belongs to a pass that died. */
const PASS_DEADLINE_MS = 30_000;
/** A tool call waits (at most WALK_DEADLINE_MS) for a pass when none finished this recently. */
const STALE_MS = 5_000;
const SHUTDOWN_WAIT_MS = 1_500;
/** guard.log moves to guard.log.1 past this size, so it holds at most about twice this. */
const LOG_MAX_BYTES = 1_000_000;
const DENIED = /Permission denied|EACCES|Errno 13|PermissionError/;

const octal = (mode: number) => mode.toString(8).padStart(4, "0");

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
export async function shellHeldFiles(): Promise<Holder[]> {
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
						holders.push({ pid: Number(pid), fd: Number(fd), cmdline, dev: st.dev, ino: st.ino, file });
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

/** The way back that needs no agent, for a script an older guard left read-only. */
const RESTORE_HINT = "If a script stays read-only after its run has ended, `running-script-guard-restore` (in ~/.dotfiles/scripts) gives back every write bit an older running-script guard took; it needs no omp session.";

function refusal(holder: Holder, toolName: string): string {
	return [
		`Running-script guard: nothing in this ${toolName} call ran. ${holder.file} is being run by pid ${holder.pid} (${holder.cmdline}).`,
		`${toolName} rewrites the file in place. The running shell reads its script from the file as it goes, so the new version would make it run shifted or garbled lines.`,
		`Wait for pid ${holder.pid} to finish, then send the call again; or write the new version to another name and \`mv\` it over ${path.basename(holder.file)} (a rename leaves the running copy reading the old file).`,
		RESTORE_HINT,
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

function errorText(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/** `work`'s result, or "expired" if `ms` pass first (`work` goes on); the timer is cleared either way. */
async function within<T>(work: Promise<T>, ms: number): Promise<T | "expired"> {
	const { promise: expired, resolve: expire } = Promise.withResolvers<"expired">();
	const timer = setTimeout(() => expire("expired"), ms);
	try {
		return await Promise.race([work, expired]);
	} finally {
		clearTimeout(timer);
	}
}

/** The give-back of write bits that older guards took: passes over their records, and the line a denied call gets. */
function createProtector(o: GuardOptions, log: { debug(message: string, data: Record<string, unknown>): void; warn(message: string, data: Record<string, unknown>): void }) {
	const debug = (message: string, data: Record<string, unknown>) => log.debug(message, data);
	const logPath = path.join(o.stateDir, "guard.log");
	const recordDir = path.join(o.stateDir, "held");
	const lockPath = path.join(o.stateDir, "pass.lock");
	const stampPath = path.join(o.stateDir, "pass.done");
	const recordPath = (file: string) => path.join(recordDir, `${Bun.hash(file).toString(16)}.json`);
	let inFlight: Promise<void> | undefined;

	/** Takes the pass lock, unless a pass finished within the interval or another one holds the lock. */
	function claim(force: boolean): boolean {
		const now = Date.now();
		const stamp = fsSync.statSync(stampPath, { throwIfNoEntry: false });
		if (!force && stamp && now - stamp.mtimeMs < o.passIntervalMs) return false;
		fsSync.mkdirSync(recordDir, { recursive: true });
		const lock = fsSync.statSync(lockPath, { throwIfNoEntry: false });
		if (lock && now - lock.mtimeMs > 2 * PASS_DEADLINE_MS) fsSync.rmSync(lockPath, { force: true });
		try {
			fsSync.closeSync(fsSync.openSync(lockPath, "wx"));
			return true;
		} catch {
			return false; // another process's pass holds it
		}
	}

	async function readRecords(): Promise<Map<string, Entry>> {
		const records = new Map<string, Entry>();
		const names = await fs.readdir(recordDir).catch(() => [] as string[]);
		await Promise.all(
			names
				.filter(name => name.endsWith(".json"))
				.map(async name => {
					try {
						const entry = JSON.parse(await fs.readFile(path.join(recordDir, name), "utf8")) as Entry;
						records.set(entry.path, entry);
					} catch {
						// Removed by a concurrent pass.
					}
				}),
		);
		return records;
	}

	/** A line in guard.log in the state directory, for a person who asks what the guard did to a file it did not take bits from. */
	async function journal(line: string): Promise<void> {
		try {
			if (((await fs.stat(logPath).catch(() => undefined))?.size ?? 0) > LOG_MAX_BYTES) await fs.rename(logPath, `${logPath}.1`);
			await fs.appendFile(logPath, `${new Date().toISOString()} ${line}\n`);
		} catch (error) {
			debug("running-script-guard: could not write guard.log", { error: errorText(error) });
		}
	}

	/** Gives back the write bits each record stands for, whether a shell still runs the file or not, and settles the record. */
	async function pass(): Promise<void> {
		const uid = process.getuid?.();
		for (const [file, record] of await readRecords()) {
			try {
				const st = await fs.stat(file).catch(() => undefined);
				const same = st?.dev === record.dev && st.ino === record.ino;
				// The file at the record's path, with exactly the mode the guard left, while the record still
				// stands (a concurrent pass may have settled it). On another inode it is a copy a rename writer
				// made after the bits were gone, carrying the mode it found: the one file the guard gives bits
				// to that it did not take them from, so it says so in omp's log as well as its own.
				if (st && st.uid === uid && (st.mode & MODE_BITS) === record.reduced && (same || st.ctimeMs >= record.at) && fsSync.existsSync(recordPath(file))) {
					await fs.chmod(file, record.mode);
					if (same) debug("running-script-guard: gave back the write bits", { file, from: octal(record.reduced), to: octal(record.mode) });
					else {
						const line = `gave back the write bits of ${file} to a copy a rename writer made after the guard took them: ${octal(record.reduced)} -> ${octal(record.mode)} (inode ${st.ino}; the guard took them from inode ${record.ino})`;
						log.warn(`running-script-guard: ${line}`, { file });
						await journal(line);
					}
				}
				await fs.rm(recordPath(file), { force: true });
			} catch (error) {
				debug("running-script-guard: could not give the write bits back", { file, error: errorText(error) });
			}
		}
	}

	/** Starts a pass unless one ran within the interval (or `force`), and waits for it at most `waitMs`. */
	async function kick(waitMs: number, force = false): Promise<void> {
		try {
			if (!inFlight && claim(force)) {
				inFlight = within(pass(), PASS_DEADLINE_MS)
					.then(outcome => {
						if (outcome === "expired") debug("running-script-guard: pass abandoned past its deadline", { deadlineMs: PASS_DEADLINE_MS });
					})
					.catch(error => debug("running-script-guard: pass failed", { error: errorText(error) }))
					.finally(() => {
						try {
							fsSync.writeFileSync(stampPath, "");
							fsSync.rmSync(lockPath, { force: true });
						} catch (error) {
							debug("running-script-guard: could not release the pass lock", { error: errorText(error) });
						}
						inFlight = undefined;
					});
			}
			if (inFlight && waitMs > 0) await within(inFlight, waitMs);
		} catch (error) {
			debug("running-script-guard: pass not started", { error: errorText(error) });
		}
	}

	/** Session end forces one pass, so bits an older guard took while this session lived come back. */
	async function end(waitMs: number): Promise<void> {
		const deadline = Date.now() + waitMs;
		try {
			if (inFlight) await within(inFlight, waitMs);
			await kick(Math.max(0, deadline - Date.now()), true);
		} catch (error) {
			debug("running-script-guard: session-end pass not run", { error: errorText(error) });
		}
	}

	/**
	 * Before a tool call: start a pass if one is due. Wait for it only when no pass finished in the
	 * last STALE_MS (a quiet machine, or a session's first call), and then at most WALK_DEADLINE_MS.
	 */
	async function beforeCall(): Promise<void> {
		let stale = true;
		try {
			const stamp = fsSync.statSync(stampPath, { throwIfNoEntry: false });
			stale = !stamp || Date.now() - stamp.mtimeMs > STALE_MS;
		} catch {
			// No state directory yet: stale.
		}
		await kick(stale ? Math.max(o.passWaitMs, WALK_DEADLINE_MS) : o.passWaitMs);
	}

	/** One line per recorded script a denied call's output names (as written, or relative to `cwd`), or nothing. */
	async function explain(text: string, cwd: string): Promise<string | undefined> {
		if (!DENIED.test(text)) return undefined;
		const words = new Set(text.split(/[\s'"`:,()[\]]+/).flatMap(word => (word ? [path.resolve(cwd, word)] : [])));
		const named = [...(await readRecords()).values()].filter(record => words.has(record.path));
		if (named.length === 0) return undefined;
		const lines = named.map(
			record =>
				`Running-script guard: ${record.path} is read-only because an older running-script guard, still loaded in an omp session that started before this one, took its write bits while pid ${record.pid} (${record.cmdline}) ran it; this session's next tool call gives them back. Write the new version to another name and \`mv\` it over ${path.basename(record.path)} (the running copy keeps reading the old file), or wait for the run to end.`,
		);
		return [...lines, RESTORE_HINT].join("\n");
	}

	return { kick, beforeCall, end, explain };
}

export function createRunningScriptGuard(options: GuardOptions) {
	return (pi: ExtensionAPI): void => {
		const debug = (message: string, data: Record<string, unknown>) => pi.logger.debug(message, data);
		const protector = fsSync.existsSync("/proc/self/fd") ? createProtector(options, pi.logger) : undefined;
		pi.on("session_start", () => void protector?.kick(0));
		pi.on("turn_start", () => void protector?.kick(0));
		// omp gives a session_shutdown handler 2 s.
		pi.on("session_shutdown", () => protector?.end(Math.max(SHUTDOWN_WAIT_MS, options.passWaitMs)));
		pi.on("tool_result", async (event: ResultEvent, ctx: Ctx) => {
			if (!protector) return undefined;
			const text = event.content.flatMap(part => (part.type === "text" && part.text ? [part.text] : [])).join("\n");
			const note = await protector.explain(text, ctx.cwd).catch(() => undefined);
			// On its own line: some tools' output does not end with one.
			return note ? { content: [...event.content, { type: "text", text: `\n${note}` }] } : undefined;
		});
		pi.on("tool_call", async (event: CallEvent, ctx: Ctx) => {
			await protector?.beforeCall();
			if (event.toolName !== "edit" && event.toolName !== "write" && event.toolName !== "ast_edit") return undefined;
			try {
				const targets = targetsOf(event.toolName, event.input)
					.map(raw => resolveTarget(raw, ctx.cwd))
					.filter((t): t is string => t !== undefined);
				if (targets.length === 0) return undefined;
				// omp refuses the call if this handler outlives its deadline, and a /proc read can hang:
				// past WALK_DEADLINE_MS the check is abandoned and the call runs.
				const found = await within(findHolder(targets, event.toolName === "ast_edit"), WALK_DEADLINE_MS);
				if (found === "expired") {
					debug("running-script-guard: check took longer than its deadline; skipped", { tool: event.toolName, deadlineMs: WALK_DEADLINE_MS });
					return undefined;
				}
				if (found) return { block: true, reason: refusal(found, event.toolName) };
			} catch (error) {
				debug("running-script-guard: check skipped", { tool: event.toolName, error: errorText(error) });
			}
			return undefined;
		});
	};
}

/** Each pid namespace keeps its own records: a pass sees only its namespace's shells, so a pass in a container must not give back what the host took. */
function defaultStateDir(): string {
	const base = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
	let namespace = "pid";
	try {
		namespace = `pid-${fsSync.readlinkSync("/proc/self/ns/pid").replace(/\D/g, "")}`;
	} catch {
		// No /proc: the protector is off anyway.
	}
	return path.join(base, "omp-running-script-guard", namespace);
}

export default createRunningScriptGuard({ stateDir: defaultStateDir(), passIntervalMs: PASS_INTERVAL_MS, passWaitMs: 0 });
