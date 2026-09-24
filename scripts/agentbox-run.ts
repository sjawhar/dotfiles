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
// `omp --resume <id> .` is spawned through the shim on PATH - so the new
// process picks up the current mise pin, plugin tree and config while the
// container, its workspace and /tmp stay. The `.` is the resumed session's
// first message: without it the agent sits idle at the prompt until someone
// types, since nothing else tells it the restart is over. The restart request is consumed
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
import * as fs from "node:fs";

// Launch flags that pick a session; the relaunch supplies its own --resume.
// Mirrors SESSION_SOURCE_FLAGS in omp's cli/flag-tables.ts. Value arity is
// spelled out here because only these flags are touched; everything else is
// passed through untouched, in order.
const SESSION_FLAGS_WITH_VALUE: Record<string, true> = { "--resume": true, "-r": true, "--session": true, "--fork": true };
const SESSION_FLAGS_BARE: Record<string, true> = { "--continue": true, "-c": true, "--from-claude": true, "--from-codex": true };
const SESSION_FILE = /\/\.omp\/agent\/sessions\/[^/]+\/[0-9T:-]+Z_([0-9a-f-]{36})\.jsonl$/;

function relaunchArgv(argv: string[], sid: string): string[] {
	const kept: string[] = [];
	for (let i = 0; i < argv.length; i++) {
		const arg = argv[i];
		const flag = arg.startsWith("--") ? arg.split("=", 1)[0] : arg;
		if (SESSION_FLAGS_BARE[flag]) continue;
		if (SESSION_FLAGS_WITH_VALUE[flag]) {
			// `--resume` may stand alone (session picker); a following token
			// that is not a flag is its value and goes with it.
			if (!arg.includes("=") && argv[i + 1] !== undefined && !argv[i + 1].startsWith("-")) i++;
			continue;
		}
		kept.push(arg);
	}
	return [...kept, "--resume", sid, "."];
}

// The session to resume: the file omp holds open (omp exports OMP_SESSION_ID
// to its children, not itself), else the newest session file of this box's
// working directory - an idle session holds no writer after a resume or a
// full-file rewrite. Every session started or resumed in the box lives under
// ~/.omp/agent/sessions/-boxes-<box>-<repo…>/ (omp keys the directory by cwd
// and relocates a resumed session's file into it), and a box name is never
// reused, so the newest file there is this box's current session.
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
	const prefix = `-boxes-${process.env.AGENTBOX_BOX}-`;
	let newest: { path: string; mtimeMs: number } | undefined;
	for (const dir of fs.readdirSync(root)) {
		if (!dir.startsWith(prefix)) continue;
		for (const file of fs.readdirSync(`${root}/${dir}`)) {
			if (!file.endsWith(".jsonl")) continue;
			const p = `${root}/${dir}/${file}`;
			const mtimeMs = fs.statSync(p).mtimeMs;
			if (!newest || mtimeMs > newest.mtimeMs) newest = { path: p, mtimeMs };
		}
	}
	return newest ? SESSION_FILE.exec(newest.path)?.[1] : undefined;
}

function spawnOmp(argv: string[]) {
	return Bun.spawn(argv, {
		stdio: ["inherit", "inherit", "inherit"],
		env: { ...process.env, AGENTBOX_RUNNER_PID: String(process.pid) },
	});
}

const launchArgv = process.argv.slice(2);
if (launchArgv[0] !== "omp") {
	process.stderr.write("agentbox-run: usage: agentbox-run.ts omp [args…]\n");
	process.exit(2);
}

let child = spawnOmp(launchArgv);
let relaunch: string[] | undefined;
let restarting = false;
let shuttingDown = false;

process.on("SIGUSR1", () => {
	if (shuttingDown || restarting) return;
	restarting = true;
	const sid = sessionId(child.pid);
	if (!sid) {
		process.stderr.write("agentbox-run: this box has no session yet; omp left running, restart not performed\n");
		restarting = false;
		return;
	}
	relaunch = relaunchArgv(launchArgv, sid);
	child.kill("SIGHUP");
});
for (const sig of ["SIGHUP", "SIGTERM"] as const) {
	process.on(sig, () => {
		shuttingDown = true;
		relaunch = undefined;
		child.kill(sig);
	});
}
// Ctrl-C reaches omp itself (same foreground process group); only make sure
// no relaunch follows.
process.on("SIGINT", () => {
	shuttingDown = true;
	relaunch = undefined;
});

for (;;) {
	const code = await child.exited;
	if (shuttingDown || relaunch === undefined) process.exit(code ?? 1);
	const argv = relaunch;
	relaunch = undefined;
	restarting = false;
	child = spawnOmp(argv);
}
