// Refuse an `edit`, `write` or `ast_edit` call that would rewrite a shell script a shell is running.
//
// omp's file tools rewrite a file in place, on the same inode. bash (and dash, busybox sh, zsh,
// ksh) does not load a script into memory: it keeps the file open and reads it by byte offset,
// re-reading after each command. Rewritten under it, the running copy resumes at the old offset in
// the new bytes and runs half-lines or different commands. Two long jobs were lost this way on
// 2026-10-24 and 2026-10-25 (dispatch://INFRA-435).
//
// Before such a call, this resolves each target (against the session cwd, through symlinks) and
// looks for a shell process of the current user holding the same device and inode open on a high
// fd (10 or above: bash's 255, dash's and busybox sh's 10). Shells put a script they run there;
// `< data.txt` or `exec 3>log` stay below 10, and `source` reads its file whole. A `tail -f`, an
// editor or a Python process holding the file is not a shell, so it never blocks. On a match the
// call is refused with the file, the pid, its command line and what to do instead.
//
// Edit targets come from omp's own parser (`editInspect` in pi-natives), so every edit mode's
// headers count, multi-file hashline and apply_patch input included. ast_edit's `paths` may name a
// directory or a glob: a running script under the directory or matching the glob counts.
//
// It fails open: no /proc (macOS), an unreadable process, a payload that does not parse, or any
// exception lets the call through and is logged at debug level.
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
const GLOB_CHARS = /[*?[{]/;

/** Raw target strings a call names, by tool. */
function targetsOf(toolName: string, input: Record<string, unknown>): string[] {
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

function isShell(exe: string, argv: string[]): boolean {
	const name = path.basename(exe);
	if (name !== "busybox") return SHELLS[name] === true;
	const applet = path.basename(argv[0] ?? "");
	return SHELLS[applet === "busybox" ? (argv[1] ?? "") : applet] === true;
}

/** Every file the current user's shells (other than this process) hold open on a high fd. */
async function shellHeldFiles(): Promise<Holder[]> {
	const uid = process.getuid?.();
	const holders: Holder[] = [];
	const pids = (await fs.readdir("/proc")).filter(name => /^\d+$/.test(name) && Number(name) !== process.pid);
	await Promise.all(
		pids.map(async pid => {
			try {
				const proc = `/proc/${pid}`;
				if ((await fs.stat(proc)).uid !== uid) return;
				const argv = (await fs.readFile(`${proc}/cmdline`, "utf8")).split("\0").filter(Boolean);
				if (!isShell(await fs.readlink(`${proc}/exe`), argv)) return;
				for (const fd of await fs.readdir(`${proc}/fd`)) {
					if (Number(fd) < MIN_SCRIPT_FD) continue;
					try {
						const st = await fs.stat(`${proc}/fd/${fd}`);
						if (!st.isFile()) continue;
						const file = (await fs.readlink(`${proc}/fd/${fd}`)).replace(/ \(deleted\)$/, "");
						holders.push({ pid: Number(pid), cmdline: argv.join(" "), dev: st.dev, ino: st.ino, file });
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

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event: CallEvent, ctx: Ctx) => {
		if (event.toolName !== "edit" && event.toolName !== "write" && event.toolName !== "ast_edit") return undefined;
		try {
			const targets = targetsOf(event.toolName, event.input)
				.map(raw => resolveTarget(raw, ctx.cwd))
				.filter((t): t is string => t !== undefined);
			if (targets.length === 0) return undefined;
			const holders = await shellHeldFiles();
			if (holders.length === 0) return undefined;
			for (const target of targets) {
				const holder = await matchTarget(target, holders, event.toolName === "ast_edit");
				if (holder) return { block: true, reason: refusal(holder, event.toolName) };
			}
		} catch (error) {
			pi.logger.debug("running-script-guard: check skipped", { tool: event.toolName, error: error instanceof Error ? error.message : String(error) });
		}
		return undefined;
	});
}
