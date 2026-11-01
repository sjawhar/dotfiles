// Keep a script from being rewritten under the shell that is running it.
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
// Two protections, both Linux-only (no /proc, no protection):
//
// 1. Refusal. An `edit`, `write` or `ast_edit` call whose target a shell is running is refused with
//    the file, the pid, its command line and what to do instead: by inode, and by path once a pass
//    has put a copy there (2, below), since the call would otherwise reach only the next run. Edit
//    targets come from omp's own parser (`editInspect` in pi-natives), so every edit mode's headers
//    count; ast_edit's `paths` may name a directory or a glob, and a running script under it counts.
//    The check fails open (the call runs, one debug line goes to omp's log) on any exception and
//    when it takes longer than WALK_DEADLINE_MS (2 s; OMP_RUNNING_SCRIPT_GUARD_MS overrides it):
//    omp refuses a call whose tool_call handler outlives `extensionHandlers.toolCallTimeoutMs`, and
//    a /proc read can hang.
//
// 2. The running copy. A refusal can only read a call's text, and every other tool writes without
//    naming its target: a bash call can write through python, perl, node, a redirect, `cp`, `tee`,
//    `dd`, or a script that writes the script, and eval writes from its own kernel. Measured on
//    2026-10-31, 19 of 29 bash writers rewrote a running script in place and corrupted the run; the
//    refusal, which never looked at bash, stopped none. The other 10 (`sed -i`, `perl -i`, `mv`,
//    `install`, `rsync`, `patch`, ...) put a new file at the path, and the run, still reading the
//    file the shell opened, finished as written.
//
//    So the guard makes that rename itself. While an agent's shell runs a script, a pass copies the
//    file — content, mode, owner, timestamps, ACL and xattrs — and renames the copy over the path.
//    The shell reads on from the file it opened, which no path reaches any more and nothing can
//    rewrite, whoever it is; a writer gets the copy and may rewrite it freely, and what it writes is
//    what the next run of that path runs. All 29 writers then leave the run finishing as written,
//    and so does eval's Python. It is what the refusal tells an agent to do by hand ("write the new
//    version to another name and `mv` it over"), done for it in advance.
//
//    Until 2026-11-01 a pass took the file's write bits instead, and an in-place open failed with
//    EACCES. That protects a run while it lasts, but it outlives it: the bits came back only on a
//    later pass, up to 5 s after the shell exited, so a test that writes a step script, runs it, and
//    rewrites the same path for the next step got PermissionError on the rewrite. 4 to 6 of the 13
//    tests of an oss-verify-proofs-internal suite failed that way under OMPCODE=1 on 2026-11-01 and
//    passed under `env -u OMPCODE`. No give-back can be prompt enough: the rewrite comes
//    microseconds after the exit, and measured that day a give-back driven by the shell's own exit
//    through a pidfd still left 32 of 50 rewrites refused, because the writer's `wait` and the
//    watcher wake on the one event. A protection the file carries outlives the run; one the path
//    carries ends with it.
//
//    The write bits are still what protects a running script no copy can replace: one with a second
//    link (the copy would leave that other name on the old file), one past DETACH_MAX_BYTES, or one
//    whose directory this user cannot write. An agent's shell is one whose environment carries
//    OMPCODE=1, which omp sets for every process it starts (kept through nohup, setsid and tmux);
//    scripts that people, cron or systemd run are left exactly as they are, as are a file another
//    user owns, which this user may neither replace nor chmod, and one with no write bits, which
//    nothing can rewrite in place anyway.
//
//    Before a pass takes bits it writes a record (the path, inode, original mode, the mode it left)
//    in the state directory, `omp-running-script-guard/pid-<pid namespace>` under $XDG_STATE_HOME or
//    ~/.local/state, so whichever omp session runs the next pass gives the bits back, even if the
//    session that took them was killed. It gives them back only to the file it took them from, and
//    only while that file still has exactly the mode it left: the same inode, or a copy that a
//    rename writer made of it after the bits were gone (`sed -i` and five other rename writers copy
//    the mode they find, so without this a `sed -i` would leave the script read-only for good), and
//    only while the guard's record for that path still stands. Bits given to such a copy are the one
//    change to a file the guard did not take them from, so omp's log gets a warning for each. A
//    later chmod by anyone else wins, and the guard never takes the bits a second time from a file
//    it has a record for: where it can, it puts a copy at the path instead, carrying the mode the
//    record holds, and that settles the record. Each copy a rename writer made, and each run of
//    running-script-guard-restore, gets a line in guard.log in the state directory; the ordinary
//    work of a pass (several a minute on a busy box) goes only to omp's debug log, and the records
//    in `held/` say which files are protected by their mode now.
//
//    Session start, every turn start and every tool call start a pass, at most one per
//    PASS_INTERVAL_MS (2 s) across all omp processes of the user (a lock and a stamp in the state
//    directory). On a loaded box a walk takes hundreds of milliseconds, so a tool call does not wait
//    for a pass while one finished within STALE_MS (5 s); the pass a turn start begins runs while the
//    model writes its tool calls. When none did (a quiet machine, or a session's first call), the
//    call waits for its own pass, at most WALK_DEADLINE_MS. While any record remains, the process
//    that ran the last pass runs another FOLLOW_UP_MS (5 s) later, idle or not.
//    At session end, the last live omp session of the user (each registers a file named for its
//    process) gives every record back, running or not, since no agent is left to write; any other
//    session forces one pass. So a script is protected within about one walk of the first turn start
//    or tool call after it starts. A file the write bits protect keeps them until a pass finds that
//    no shell runs it, and if the last omp process is killed first, until the next omp session's
//    first pass; scripts/running-script-guard-restore gives every record back at once, with no omp
//    session. A call that fails on such a file gets lines appended that say why, what to do, and
//    that command.
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
/** One script whose write bits a pass took, no copy being possible: `mode` is the mode before, `reduced` the mode it left, `at` when. */
type Entry = { path: string; dev: number; ino: number; mode: number; reduced: number; at: number; pid: number; cmdline: string };

export type GuardOptions = {
	/** Shared by every omp process of this user in this pid namespace: the records, the pass lock and stamp. */
	stateDir: string;
	passIntervalMs: number;
	/** How long a tool call waits for a pass it started: 0 in production; tests wait for the whole pass. */
	passWaitMs: number;
	/** While a record remains, the process that ran the last pass runs another this long after it. */
	followUpMs: number;
	/** The running scripts a pass considers; tests narrow it to their own. */
	discover: () => Promise<Holder[]>;
	/**
	 * Whether a pass protects this shell's script. Tests stand in for it and mark their own shells
	 * another way, so their scripts carry no OMPCODE=1 and no other session's guard acts on them.
	 */
	agentRun: (pid: number) => Promise<boolean>;
};

const SHELLS: Record<string, true> = { bash: true, sh: true, dash: true, zsh: true, ksh: true, mksh: true, ash: true, hush: true };
const MIN_SCRIPT_FD = 10;
const O_ACCMODE = 0o3;
const O_CLOEXEC = 0o2000000;
const MODE_BITS = 0o7777;
const WRITE_BITS = 0o222;
/** The largest running script a pass copies; the fallback below covers anything above it. */
const DETACH_MAX_BYTES = 8 * 1024 * 1024;
const GLOB_CHARS = /[*?[{]/;
const WALK_DEADLINE_MS = Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) > 0 ? Number(process.env.OMP_RUNNING_SCRIPT_GUARD_MS) : 2_000;
const PASS_INTERVAL_MS = 2_000;
/** A pass running longer is abandoned, and a lock older than twice this belongs to a pass that died. */
const PASS_DEADLINE_MS = 30_000;
const FOLLOW_UP_MS = 5_000;
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

/** Whether omp started this process or an ancestor of it: omp sets OMPCODE=1 for every process it starts. The default `GuardOptions.agentRun`. */
async function agentRun(pid: number): Promise<boolean> {
	const environ = await fs.readFile(`/proc/${pid}/environ`, "utf8").catch(() => "");
	return environ.split("\0").includes("OMPCODE=1");
}

/** The holder whose file this target would rewrite, if any. */
async function matchTarget(target: string, holders: Holder[], globs: boolean): Promise<Holder | undefined> {
	// A target that does not exist is a new file, or (for ast_edit) a glob.
	const st: Stats | undefined = await fs.stat(target).catch(() => undefined);
	if (st?.isFile()) {
		const real = await fs.realpath(target).catch(() => target);
		// By inode for a run no pass has reached yet, and by path for one whose copy a pass has
		// already put there: in both the call's new version must not go to the file being run.
		return holders.find(h => (h.dev === st.dev && h.ino === st.ino) || h.file === real);
	}
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

/** The way back that needs no agent, for a script left read-only after its run (for example, when every omp process was killed). */
const RESTORE_HINT = "If a script stays read-only after its run has ended, `running-script-guard-restore` (in ~/.dotfiles/scripts) gives back every write bit the guard took; it needs no omp session.";

function refusal(holder: Holder, toolName: string): string {
	return [
		`Running-script guard: nothing in this ${toolName} call ran. ${holder.file} is being run by pid ${holder.pid} (${holder.cmdline}).`,
		`${toolName} rewrites the file in place. The running shell reads its script from the file as it goes, so the new version would make it run shifted or garbled lines; and once a pass has put a copy of the script at this path, the shell reads the file it opened, so the call would reach only the next run.`,
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

/**
 * Puts a copy of a running script at its path, which leaves the shell reading a file no path
 * reaches: a writer gets the copy, the run finishes as written, and nothing has to be given back
 * when it ends. `mode` is the mode the file carries without the guard. Returns false when no copy
 * was made — another link to the inode, a file past DETACH_MAX_BYTES, a directory this user cannot
 * write, or any other failure — so that the caller takes the write bits instead.
 */
async function detach(holder: Holder, st: Stats, mode: number): Promise<boolean> {
	if (st.nlink !== 1 || st.size > DETACH_MAX_BYTES) return false;
	// The fd must still be the file at this path. A wrapper that execs another script keeps the
	// same pid while its fd moves to the new file, so a copy taken from the fd and renamed over
	// holder.file would write the other script's bytes here, with mode, owner and timestamps
	// preserved, leaving a valid executable that is the wrong program and that no check short of
	// comparing content can see. On 2026-11-01 shims/omp was left byte-identical to the
	// scripts/agent-session it execs, and every new omp launch on the box failed for ten minutes.
	const open = await fs.stat(`/proc/${holder.pid}/fd/${holder.fd}`).catch(() => undefined);
	if (open?.dev !== holder.dev || open.ino !== holder.ino) return false;
	const temp = path.join(path.dirname(holder.file), `.${path.basename(holder.file)}.omp-guard-${process.pid}-${Date.now().toString(36)}`);
	try {
		// From the shell's own fd, so the copy is of the file it runs, and through `cp`, so the mode,
		// the owner, the timestamps, the ACL and the xattrs come with it.
		const copy = Bun.spawn(["cp", "--preserve=mode,ownership,timestamps,xattr", "--reflink=auto", "-T", `/proc/${holder.pid}/fd/${holder.fd}`, temp], { stdout: "ignore", stderr: "ignore" });
		if ((await copy.exited) !== 0) return false;
		if ((st.mode & MODE_BITS) !== mode) await fs.chmod(temp, mode); // give back bits an earlier pass took
		// Only over the file the shell runs: a rename writer that got in first has put its own file
		// at the path, and that file is both safe and newer than this copy.
		const now = await fs.stat(holder.file).catch(() => undefined);
		if (now?.dev !== holder.dev || now.ino !== holder.ino) return true;
		await fs.rename(temp, holder.file);
		return true;
	} catch {
		return false; // the caller falls back to the write bits
	} finally {
		await fs.rm(temp, { force: true }); // nothing to remove once the rename took it
	}
}

/** The protection of the running copy: passes, the records of what they could only chmod, and the line a denied call gets. */
function createProtector(o: GuardOptions, log: { debug(message: string, data: Record<string, unknown>): void; warn(message: string, data: Record<string, unknown>): void }) {
	const debug = (message: string, data: Record<string, unknown>) => log.debug(message, data);
	const logPath = path.join(o.stateDir, "guard.log");
	const recordDir = path.join(o.stateDir, "held");
	const lockPath = path.join(o.stateDir, "pass.lock");
	const stampPath = path.join(o.stateDir, "pass.done");
	const recordPath = (file: string) => path.join(recordDir, `${Bun.hash(file).toString(16)}.json`);
	// One file per live session, named for its process, so the last one to end can tell it is the last.
	const sessionDir = path.join(o.stateDir, "sessions");
	const sessionPath = path.join(sessionDir, `${process.pid}-${crypto.randomUUID()}`);
	let inFlight: Promise<void> | undefined;
	let followUpTimer: NodeJS.Timeout | undefined;
	try {
		fsSync.mkdirSync(sessionDir, { recursive: true });
		fsSync.writeFileSync(sessionPath, "");
	} catch (error) {
		debug("running-script-guard: session not registered", { error: errorText(error) });
	}

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

	/** Written whole or not at all, so a pass that dies mid-write leaves no record half-read. */
	async function writeRecord(entry: Entry): Promise<void> {
		const target = recordPath(entry.path);
		const temp = `${target}.${process.pid}.tmp`;
		await fs.writeFile(temp, JSON.stringify(entry));
		await fs.rename(temp, target);
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

	/**
	 * Protects each newly running script and settles every record that no longer holds; returns how
	 * many records remain. `release` protects nothing and gives every record back: the last omp
	 * session is ending, so no agent is left to write.
	 */
	async function pass(release: boolean): Promise<number> {
		const uid = process.getuid?.();
		// The script an agent's shell runs at each path now. A holder whose path was renamed over or
		// deleted holds an inode no path reaches, so nothing can write it in place any more.
		const running = new Map<string, { holder: Holder; st: Stats }>();
		await Promise.all(
			(release ? [] : await o.discover()).map(async holder => {
				const st = await fs.stat(holder.file).catch(() => undefined);
				if (st?.dev === holder.dev && st.ino === holder.ino && (await o.agentRun(holder.pid))) running.set(holder.file, { holder, st });
			}),
		);
		const records = await readRecords();
		for (const [file, { holder, st }] of running) {
			const record = records.get(file);
			if (st.uid !== uid) continue; // not ours to change
			const mode = st.mode & MODE_BITS;
			// A record stands for write bits a fallback take removed. Any other mode is one somebody
			// else set meanwhile, and it wins.
			if (record && mode !== record.reduced) continue;
			const original = record ? record.mode : mode;
			if ((original & WRITE_BITS) === 0) {
				if (record) await fs.rm(recordPath(file), { force: true }); // read-only by someone's choice
				continue;
			}
			try {
				if (await detach(holder, st, original)) {
					// The path carries the protection now, so a record for it is settled: the copy left
					// there has the mode the file had before the guard saw it.
					if (record) await fs.rm(recordPath(file), { force: true });
					debug("running-script-guard: put a copy at the path", { file, mode: octal(original), pid: holder.pid, cmdline: holder.cmdline });
					continue;
				}
				if (record) continue; // no copy can be made, and its bits are already gone
				const entry: Entry = { path: file, dev: holder.dev, ino: holder.ino, mode, reduced: mode & ~WRITE_BITS, at: Date.now(), pid: holder.pid, cmdline: holder.cmdline };
				// The same hazard as the copy: a wrapper that execs another script keeps its pid while
				// the fd moves, so a chmod through the fd would take the write bits of the OTHER file
				// while the record names this path. The give-back then never matches, the record is
				// dropped, and that file is left read-only with nothing to restore it. On 2026-11-01
				// scripts/agent-session was left 555 with no record that way.
				const open = await fs.stat(`/proc/${holder.pid}/fd/${holder.fd}`).catch(() => undefined);
				if (open?.dev !== holder.dev || open.ino !== holder.ino) continue;
				// The record first: if this process dies after the chmod, the next pass still knows
				// what to give back. Through the shell's own fd, so the chmod lands on the inode it runs.
				await writeRecord(entry);
				await fs.chmod(`/proc/${holder.pid}/fd/${holder.fd}`, entry.reduced);
				debug("running-script-guard: took the write bits", { file, from: octal(mode), to: octal(entry.reduced), pid: holder.pid, cmdline: holder.cmdline });
			} catch (error) {
				debug("running-script-guard: could not protect the running copy", { file, error: errorText(error) });
			}
		}
		for (const [file, record] of records) {
			if (running.has(file)) continue;
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
		return (await fs.readdir(recordDir).catch(() => [] as string[])).filter(name => name.endsWith(".json")).length;
	}

	/** While a record remains, this process runs another pass later, idle or not, so the bits come back without waiting for a turn. */
	function followUp(): void {
		if (followUpTimer) return;
		followUpTimer = setTimeout(() => {
			followUpTimer = undefined;
			void kick(0);
		}, o.followUpMs);
		followUpTimer.unref?.();
	}

	/** Starts a pass unless one ran within the interval (or `force`), and waits for it at most `waitMs`. */
	async function kick(waitMs: number, opts: { force?: boolean; release?: boolean } = {}): Promise<void> {
		try {
			if (!inFlight && claim(opts.force === true)) {
				inFlight = within(pass(opts.release === true), PASS_DEADLINE_MS)
					.then(outcome => {
						if (outcome === "expired") debug("running-script-guard: pass abandoned past its deadline", { deadlineMs: PASS_DEADLINE_MS });
						else if (outcome > 0 && !opts.release) followUp();
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

	/** Whether another omp session of this user in this namespace lives: a file in the sessions directory names a live pid. */
	function othersAlive(): boolean {
		for (const name of fsSync.readdirSync(sessionDir)) {
			if (fsSync.existsSync(`/proc/${Number.parseInt(name, 10)}`)) return true;
			fsSync.rmSync(path.join(sessionDir, name), { force: true }); // a session whose process died
		}
		return false;
	}

	/** Session end: the last session gives every record back, since no agent is left to write; any other forces one pass. */
	async function end(waitMs: number): Promise<void> {
		const deadline = Date.now() + waitMs;
		try {
			clearTimeout(followUpTimer);
			fsSync.rmSync(sessionPath, { force: true });
			if (inFlight) await within(inFlight, waitMs);
			await kick(Math.max(0, deadline - Date.now()), { force: true, release: !othersAlive() });
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

	/** One line per protected script a denied call's output names (as written, or relative to `cwd`), or nothing. */
	async function explain(text: string, cwd: string): Promise<string | undefined> {
		if (!DENIED.test(text)) return undefined;
		const words = new Set(text.split(/[\s'"`:,()[\]]+/).flatMap(word => (word ? [path.resolve(cwd, word)] : [])));
		const named = [...(await readRecords()).values()].filter(record => words.has(record.path));
		if (named.length === 0) return undefined;
		const lines = named.map(
			record =>
				`Running-script guard: ${record.path} is read-only while a shell runs it (pid ${record.pid}: ${record.cmdline}); the guard took its write bits because a write in place would make that shell run shifted bytes. Write the new version to another name and \`mv\` it over ${path.basename(record.path)} (the running copy keeps reading the old file), or wait for the run to end. Do not make it writable.`,
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

export default createRunningScriptGuard({ stateDir: defaultStateDir(), passIntervalMs: PASS_INTERVAL_MS, passWaitMs: 0, followUpMs: FOLLOW_UP_MS, discover: shellHeldFiles, agentRun });
