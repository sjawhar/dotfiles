#!/usr/bin/env bun
// agentbox-run: the main process of an agent box running omp. omp is its
// child; the box lives as long as this process does.
//
//   bun agentbox-run.ts omp [omp args…]
//
// Normally omp's exit is the box's exit (same code), and the launcher's
// close_box runs. SIGUSR1 asks for a restart instead: the session id is read
// from omp's open session file, omp is hung up (its ordinary graceful
// teardown: draft saved, session_shutdown handlers run), and a fresh
// `omp --resume <id>` is spawned through the shim on PATH - so the new
// process picks up the current mise pin, plugin tree and config while the
// container, its workspace and /tmp stay. The relaunch carries no message: omp
// records a positional as a user message, so the `.` this used to send counted
// as text the person typed in the transcript store. Without one the agent would
// sit idle at the prompt, so the runner starts `agentbox move --wake`, which
// sends one Envoy notice (a custom message) once the new process has
// registered. The restart request is consumed
// before the relaunch: a replacement that dies on startup ends the box
// rather than looping. SIGHUP/SIGTERM (pane kill, docker stop) and SIGINT
// mean "go away" as before - forwarded, never followed by a relaunch.
//
// omp's own /restart cannot do this: it re-execs process.execPath, the
// versioned mise install path, so it never sees a new pin.
//
// The agent triggers it from its bash tool once its work is at a safe point
// (`agentbox restart <box>` on the host tells it so over Envoy) by running
// `agentbox restart`, which sends SIGUSR1 to $AGENTBOX_RUNNER_PID - exported
// to omp and so to its tools. (omp's builtin kill refuses the shell's
// ancestors, so a bare `kill -USR1` from the bash tool is rejected.)
//
// Every restart request, omp exit and relaunch is appended to
// ~/.cache/omp/agentbox/<box>.log (a mounted path, so it outlives the box).
// While omp runs it owns the pane, where a stray line would sit on its screen,
// so only the box's last line also goes to stderr; a box that ended is
// otherwise silent.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const box = process.env.AGENTBOX_BOX ?? "unknown-box";
// The machine a move's marker names as `from=`: the host's name, which a box has as AGENTBOX_HOST.
const machine = process.env.AGENTBOX_HOST || os.hostname().split(".")[0];
const logPath = `${process.env.HOME}/.cache/omp/agentbox/${box}.log`;

function note(message: string, toStderr = false): void {
	const line = `agentbox-run ${new Date().toISOString()} ${box}: ${message}\n`;
	if (toStderr) process.stderr.write(line);
	try {
		fs.mkdirSync(path.dirname(logPath), { recursive: true });
		fs.appendFileSync(logPath, line);
	} catch (error) {
		process.stderr.write(`agentbox-run: cannot append to ${logPath}: ${error}\n`);
	}
}

// A relaunch in flight, so nothing outside this box restarts the session while its omp is down.
// `agentbox restart` hangs omp up and spawns a fresh one in the same container; for those seconds
// the session holds no transcript open, runs no process and registers nowhere, which is exactly
// what an off-box watcher reads as "dead". At 10:10Z on 2026-11-05 devbox-offbox-watch read that
// and asked the host to restart a session being restarted in place; only `agentbox new`'s own
// duplicate guard stopped there being two of it (dispatch://INFRA-993). The marker is written
// before the hang-up and removed once the new omp has registered on Envoy -- the wake notice
// already waits for that -- and `devbox-offbox-act live` prints it as a `restarting` reading.
// The directory is the log's, a mounted path, so the host can read what a box wrote.
const restartMarkerPath = (sid: string): string =>
	`${path.dirname(logPath)}/restarting-${sid}`;

function markRestarting(sid: string): void {
	try {
		fs.mkdirSync(path.dirname(logPath), { recursive: true });
		fs.writeFileSync(
			restartMarkerPath(sid),
			`session=${sid}\nbox=${box}\nmachine=${machine}\nat=${Math.floor(Date.now() / 1000)}\nrunner=${process.pid}\n`,
		);
	} catch (error) {
		// A marker that cannot be written must not stop a restart the agent asked for: the watcher
		// still has `agentbox new`'s duplicate guard behind it. Say so in the log and go on.
		note(`could not write the relaunch marker for ${sid}: ${error}`);
	}
}

function clearRestarting(sid: string): void {
	try {
		fs.rmSync(restartMarkerPath(sid), { force: true });
	} catch (error) {
		note(`could not remove the relaunch marker for ${sid}: ${error}`);
	}
}

// Relaunch argument filtering mirrors `flagConsumesValue` and
// `restartArgv` in omp v18.4.9-sami.20261001-204440,
// packages/coding-agent/src/cli/flag-tables.ts. It needs the full arity
// classification, not only session-source flags, so configuration values
// cannot be mistaken for positional prompts.
const STRING_VALUE_FLAGS: Record<string, true> = {
	"--cwd": true,
	"--config": true,
	"--add-dir": true,
	"--mode": true,
	"--fork": true,
	"--provider": true,
	"--model": true,
	"--smol": true,
	"--slow": true,
	"--plan": true,
	"--prewalk-into": true,
	"--plan-yolo-into": true,
	"--max-time": true,
	"--service-tier": true,
	"--reduce-motion": true,
	"--api-key": true,
	"--system-prompt": true,
	"--system-prompt-template": true,
	"--append-system-prompt": true,
	"--provider-session-id": true,
	"--prompt-cache-key": true,
	"--session-dir": true,
	"--models": true,
	"--tools": true,
	"--thinking": true,
	"--export": true,
	"--hook": true,
	"--extension": true,
	"-e": true,
	"--trusted-extension": true,
	"--plugin-dir": true,
	"--skills": true,
	"--approval-mode": true,
};
const OPTIONAL_VALUE_FLAGS: Record<string, true> = { "--resume": true, "-r": true, "--session": true };
const EXTENSION_SHADOWABLE_STRING_FLAGS: Record<string, true> = { "--plan": true };
const VALUELESS_FLAGS: Record<string, true> = {
	"--help": true,
	"--version": true,
	"--allow-home": true,
	"--continue": true,
	"--from-claude": true,
	"--from-codex": true,
	"--no-session": true,
	"--no-tools": true,
	"--no-lsp": true,
	"--no-pty": true,
	"--hide-thinking": true,
	"--advisor": true,
	"--external-thinking": true,
	"--prewalk": true,
	"--no-prewalk": true,
	"--plan-yolo": true,
	"--print": true,
	"--print-thoughts": true,
	"--no-extensions": true,
	"--no-skills": true,
	"--no-rules": true,
	"--no-title": true,
	"--no-ui": true,
	"--auto-approve": true,
	"--yolo": true,
};
const SESSION_SOURCE_FLAGS: Record<string, true> = {
	"--resume": true,
	"-r": true,
	"--session": true,
	"--continue": true,
	"-c": true,
	"--fork": true,
	"--from-claude": true,
	"--from-codex": true,
};
const SESSION_FILE = /\/\.omp\/agent\/sessions\/[^/]+\/[0-9T:-]+Z_([0-9a-f-]{36})\.jsonl$/;

function flagConsumesValue(flag: string, next: string | undefined): boolean {
	if (flag.startsWith("--") && flag.includes("=")) return false;
	if (next === undefined) return false;
	if (STRING_VALUE_FLAGS[flag]) return true;
	const valueLike = !next.startsWith("-");
	if (EXTENSION_SHADOWABLE_STRING_FLAGS[flag]) return valueLike;
	if (OPTIONAL_VALUE_FLAGS[flag]) return valueLike && next.length > 0;
	return (
		flag.startsWith("--") &&
		!flag.includes("=") &&
		!STRING_VALUE_FLAGS[flag] &&
		!OPTIONAL_VALUE_FLAGS[flag] &&
		!VALUELESS_FLAGS[flag] &&
		valueLike
	);
}

export function relaunchArgv(argv: string[], sid: string): string[] {
	const kept: string[] = [];
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i];
		if (i === 0) {
			kept.push(arg);
			continue;
		}
		if (arg === "--") break;
		if (!arg.startsWith("-")) continue;
		const consumesNext = flagConsumesValue(arg, argv[i + 1]);
		const flag = arg.startsWith("--") ? arg.split("=", 1)[0] : arg;
		if (SESSION_SOURCE_FLAGS[flag]) {
			if (consumesNext) i++;
			continue;
		}
		kept.push(arg);
		if (consumesNext) kept.push(argv[++i]);
	}
	return [...kept, "--resume", sid];
}

const RESTART_WAKE_NOTICE =
	"agentbox: the restart is done: this session was resumed in the same box on the host's current omp. Go on with the work you were doing before the restart. (An automated notice from agentbox, not a person.)";

// Wake the resumed session over Envoy (scripts/agentbox move_wake), appending to this box's log,
// and remove the relaunch marker once that session has registered -- which is what the waker
// waits for. A wake that never comes (a resume that does not register) leaves the marker to
// expire on the reader's side, which devbox-offbox-watch does after 180 s.
function wakeOverEnvoy(sid: string): void {
	try {
		Bun.spawn(["sh", "-c", 'agentbox move --wake "$1" "$2" >>"$3" 2>&1; rm -f "$4"', "sh", sid, RESTART_WAKE_NOTICE, logPath, restartMarkerPath(sid)], {
			stdio: ["ignore", "ignore", "ignore"],
		}).unref();
	} catch (error) {
		note(`could not start the Envoy waker for ${sid}: ${error}`);
		clearRestarting(sid);
	}
}

// A session `agentbox move --to <machine>` moved off this machine runs there now, and one
// session id must never run on two machines at once (dispatch://INFRA-993). The move leaves a
// marker, `agentbox-moved`, in the session's own directory (the transcript path without
// `.jsonl`) and the same file as ~/.omp/agentbox-moved/<session id>; scripts/agent-session
// refuses the same launches on the host, by the same rules. The marker counts only on the
// machine it names as `from=` (AGENTBOX_HOST in a box, which has a hostname of its own).
// Returns the refusal, or undefined when the launch may go ahead.
export function movedRefusal(argv: string[], home: string, machine: string): string | undefined {
	let target: string | undefined;
	for (let i = 1; i < argv.length; i++) {
		const arg = argv[i];
		if (arg === "--") break;
		if ((arg === "--resume" || arg === "-r" || arg === "--session") && i + 1 < argv.length) target = argv[i + 1];
		else if (arg.startsWith("--resume=") || arg.startsWith("--session=")) target = arg.slice(arg.indexOf("=") + 1);
	}
	if (!target) return undefined;
	const index = `${home}/.omp/agentbox-moved`;
	const markers: string[] = [];
	if (target.endsWith(".jsonl")) {
		markers.push(`${target.slice(0, -".jsonl".length)}/agentbox-moved`);
		const m = /_([0-9a-f-]{36})\.jsonl$/.exec(target);
		if (m) markers.push(`${index}/${m[1]}`);
	} else if (/^[0-9a-f-]+$/.test(target)) {
		try {
			for (const name of fs.readdirSync(index)) if (name.startsWith(target)) markers.push(`${index}/${name}`);
		} catch {}
	}
	for (const marker of markers) {
		let text: string;
		try {
			text = fs.readFileSync(marker, "utf8");
		} catch {
			continue;
		}
		const field = (k: string) => new RegExp(`^${k}=(.*)$`, "m").exec(text)?.[1] ?? "";
		if (field("from") !== machine) continue;
		return `this session moved to ${field("to")} at ${field("time")} (marker ${marker}); it runs there now, and one session must not run on two machines. Resume it on ${field("to")}.`;
	}
	return undefined;
}

// The session to resume: the file omp holds open (omp exports OMP_SESSION_ID
// to its children, not itself), else the newest session file of this box's
// working directory - an idle session holds no writer after a resume or a
// full-file rewrite. Every session started or resumed in the box lives under a
// ~/.omp/agent/sessions/ directory omp names after the working directory, so the
// name holds `-<box>-<repo…>` wherever the box tree is (`-boxes-<box>-<repo>` under
// $HOME, `--mnt-nvme-<user>-agentbox-boxes-<box>-<repo>--` on the big disk, agentbox's
// BOXES). A box name is never reused, so the newest file in a directory naming this
// box is its current session.
function sessionId(pid: number): string | undefined {
	try {
		for (const name of fs.readdirSync(`/proc/${pid}/fd`)) {
			try {
				const m = SESSION_FILE.exec(fs.readlinkSync(`/proc/${pid}/fd/${name}`));
				if (m) return m[1];
			} catch {}
		}
	} catch {
		return undefined; // omp is gone
	}
	const root = `${process.env.HOME}/.omp/agent/sessions`;
	const marker = `-${process.env.AGENTBOX_BOX}-`;
	let newest: { path: string; mtimeMs: number } | undefined;
	for (const dir of fs.readdirSync(root)) {
		if (!dir.includes(marker)) continue;
		for (const file of fs.readdirSync(`${root}/${dir}`)) {
			if (!file.endsWith(".jsonl")) continue;
			const p = `${root}/${dir}/${file}`;
			const mtimeMs = fs.statSync(p).mtimeMs;
			if (!newest || mtimeMs > newest.mtimeMs) newest = { path: p, mtimeMs };
		}
	}
	return newest ? SESSION_FILE.exec(newest.path)?.[1] : undefined;
}

// The Dispatch and Envoy environment of a host session, for the omp of a box: the pi-envoy
// extension sets it in the omp it loads in, but a box's tool shells on sami-devbox had none of it
// (BET-561 session 01a19e2e exported it by hand, 2026-11-03), so `dispatch` was not on PATH and
// uploads failed. Each value already set wins: DISPATCH_HOST (omp), ENVOY_CONFIG_FILE (the
// session's envoy.json, when it exists), DISPATCH_SESSION_ID (the id the launch resumes, from
// its transcript path or a bare id), and the plugin's bin directory first on PATH.
export function dispatchEnv(argv: string[], env: Record<string, string | undefined>): Record<string, string> {
	const out: Record<string, string> = {};
	const home = env.HOME ?? "";
	const dotfiles = env.DOTFILES_DIR || `${home}/.dotfiles`;
	if (!env.DISPATCH_HOST) out.DISPATCH_HOST = "omp";
	if (!env.ENVOY_CONFIG_FILE) {
		const file = `${env.XDG_CONFIG_HOME || `${home}/.config`}/legion/envoy.json`;
		if (fs.existsSync(file)) out.ENVOY_CONFIG_FILE = file;
	}
	if (!env.DISPATCH_SESSION_ID) {
		for (let i = 1; i < argv.length; i++) {
			const arg = argv[i];
			if (arg === "--") break;
			let value: string | undefined;
			if ((arg === "--resume" || arg === "-r" || arg === "--session") && i + 1 < argv.length) value = argv[i + 1];
			else if (arg.startsWith("--resume=") || arg.startsWith("--session=")) value = arg.slice(arg.indexOf("=") + 1);
			if (value === undefined) continue;
			const id = /_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$/.exec(value)?.[1] ??
				/^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.exec(value)?.[1];
			if (id) out.DISPATCH_SESSION_ID = id;
		}
	}
	const bin = `${dotfiles}/omp/plugins/node_modules/@sjawhar/pi-envoy/bin`;
	const path = env.PATH ?? "";
	if (fs.existsSync(`${bin}/dispatch`) && !path.split(":").includes(bin)) out.PATH = path ? `${bin}:${path}` : bin;
	return out;
}

function spawnOmp(argv: string[]) {
	return Bun.spawn(argv, {
		stdio: ["inherit", "inherit", "inherit"],
		env: { ...process.env, ...dispatchEnv(argv, process.env), AGENTBOX_RUNNER_PID: String(process.pid) },
	});
}

// OOM bias inside the box (dispatch://INFRA-702). At the box's memory limit the kernel kills
// the box process with the largest badness: its RSS plus oom_score_adj × limit / 1000. With
// every adj at 0 that is often omp, and the session dies with its subagents. On the host,
// scripts/omp-memory-watch raises every process in an omp scope but omp to +500, but it cannot
// write a box's processes (Sysbox maps their uid: EPERM). So the runner does it here: every
// child of the runner but omp (dockerd, the relays) and every child of omp is raised, with the
// descendants it already has, and anything forked later inherits +500 from its parent. At +500
// under a 24 GiB limit each raised process scores 12 GiB more, so among them the largest still
// goes first, and omp outlives any of them. The runner and omp stay at 0: either one dying ends
// the box. Raising an adj of one's own process needs no privilege; nothing is ever lowered.
export const OOM_BIAS_ADJ = 500;
const OOM_BIAS_INTERVAL_MS = 10_000;

export function childPids(pid: number, procRoot = "/proc"): number[] {
	const kids: number[] = [];
	let tids: string[];
	try {
		tids = fs.readdirSync(`${procRoot}/${pid}/task`);
	} catch {
		return kids; // gone
	}
	for (const tid of tids) {
		try {
			for (const word of fs.readFileSync(`${procRoot}/${pid}/task/${tid}/children`, "utf8").split(" ")) {
				const kid = Number(word);
				if (kid > 0) kids.push(kid);
			}
		} catch {}
	}
	return kids;
}

function readAdj(pid: number, procRoot: string): number | undefined {
	try {
		return Number(fs.readFileSync(`${procRoot}/${pid}/oom_score_adj`, "utf8").trim());
	} catch {
		return undefined; // gone
	}
}

// Raise `top` and every descendant it has now to `adj`; returns how many were raised.
function raiseTree(top: number, adj: number, procRoot: string): number {
	let raised = 0;
	const seen = new Set<number>();
	const stack = [top];
	while (stack.length > 0) {
		const pid = stack.pop() as number;
		if (seen.has(pid)) continue;
		seen.add(pid);
		const current = readAdj(pid, procRoot);
		if (current === undefined) continue;
		if (current < adj) {
			try {
				fs.writeFileSync(`${procRoot}/${pid}/oom_score_adj`, String(adj));
				raised++;
			} catch {}
		}
		stack.push(...childPids(pid, procRoot));
	}
	return raised;
}

// One pass. A direct child already at `adj` was raised before it forked, so its descendants
// inherited `adj` and are not walked again.
export function oomBiasPass(runnerPid: number, ompPid: number, adj = OOM_BIAS_ADJ, procRoot = "/proc"): number {
	const tops = [...childPids(runnerPid, procRoot).filter((pid) => pid !== ompPid), ...childPids(ompPid, procRoot)];
	let raised = 0;
	for (const pid of tops) {
		const current = readAdj(pid, procRoot);
		if (current !== undefined && current < adj) raised += raiseTree(pid, adj, procRoot);
	}
	return raised;
}

if (import.meta.main) {
	const launchArgv = process.argv.slice(2);
	if (launchArgv[0] !== "omp") {
		process.stderr.write("agentbox-run: usage: agentbox-run.ts omp [args…]\n");
		process.exit(2);
	}
	const home = process.env.HOME ?? "";
	const refusedAtStart = movedRefusal(launchArgv, home, machine);
	if (refusedAtStart) {
		note(`refused: ${refusedAtStart}`, true);
		process.exit(3);
	}

	let child = spawnOmp(launchArgv);
	let startedAt = Date.now();
	note(`omp started (pid ${child.pid}): ${launchArgv.join(" ")}`);
	const oomBias = setInterval(() => {
		try {
			oomBiasPass(process.pid, child.pid);
		} catch (error) {
			note(`oom bias pass failed: ${error}`);
		}
	}, OOM_BIAS_INTERVAL_MS);
	oomBias.unref();
	let relaunch: string[] | undefined;
	// The session a relaunch marker was written for, so the box's own shutdown can remove it.
	let markedSid: string | undefined;
	let restarting = false;
	let shuttingDown = false;

	process.on("SIGUSR1", () => {
		if (shuttingDown || restarting) return;
		restarting = true;
		const sid = sessionId(child.pid);
		if (!sid) {
			note("restart requested, but this box has no session yet; omp left running, restart not performed");
			restarting = false;
			return;
		}
		const refused = movedRefusal(relaunchArgv(launchArgv, sid), home, machine);
		if (refused) {
			note(`restart refused, omp left running: ${refused}`);
			restarting = false;
			return;
		}
		relaunch = relaunchArgv(launchArgv, sid);
		// Before the hang-up, never after it: the gap this marker covers starts here.
		markedSid = sid;
		markRestarting(sid);
		note(`restart requested: hanging up omp (pid ${child.pid}) to resume session ${sid}`);
		child.kill("SIGHUP");
	});
	for (const sig of ["SIGHUP", "SIGTERM"] as const) {
		process.on(sig, () => {
			shuttingDown = true;
			note(`${sig} received: shutting omp down, no relaunch`);
			relaunch = undefined;
			child.kill(sig);
			// The box is going away, so no relaunch follows and a marker from a relaunch that
			// was already in flight must not outlive it.
			if (markedSid) clearRestarting(markedSid);
		});
	}
	// Ctrl-C reaches omp itself (same foreground process group); only make sure
	// no relaunch follows.
	process.on("SIGINT", () => {
		shuttingDown = true;
		note("SIGINT received: no relaunch will follow");
		relaunch = undefined;
	});

	for (;;) {
		const code = await child.exited;
		const ran = `after ${Math.round((Date.now() - startedAt) / 1000)}s (exit code ${code ?? "none"}, signal ${child.signalCode ?? "none"})`;
		if (shuttingDown || relaunch === undefined) {
			note(`omp (pid ${child.pid}) exited ${ran}; the box ends`, true);
			process.exit(code ?? 1);
		}
		const argv = relaunch;
		relaunch = undefined;
		restarting = false;
		child = spawnOmp(argv);
		startedAt = Date.now();
		note(`omp exited ${ran} for the restart; relaunched (pid ${child.pid}): ${argv.join(" ")}`);
		wakeOverEnvoy(argv[argv.length - 1]);
	}
}
