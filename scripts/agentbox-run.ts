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
//
// Every restart request, omp exit and relaunch is appended to
// ~/.cache/omp/agentbox/<box>.log (a mounted path, so it outlives the box).
// While omp runs it owns the pane, where a stray line would sit on its screen,
// so only the box's last line also goes to stderr; a box that ended is
// otherwise silent.
import * as fs from "node:fs";
import * as path from "node:path";

const box = process.env.AGENTBOX_BOX ?? "unknown-box";
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

if (import.meta.main) {
	const launchArgv = process.argv.slice(2);
	if (launchArgv[0] !== "omp") {
		process.stderr.write("agentbox-run: usage: agentbox-run.ts omp [args…]\n");
		process.exit(2);
	}

	let child = spawnOmp(launchArgv);
	let startedAt = Date.now();
	note(`omp started (pid ${child.pid}): ${launchArgv.join(" ")}`);
	let relaunch: string[] | undefined;
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
		relaunch = relaunchArgv(launchArgv, sid);
		note(`restart requested: hanging up omp (pid ${child.pid}) to resume session ${sid}`);
		child.kill("SIGHUP");
	});
	for (const sig of ["SIGHUP", "SIGTERM"] as const) {
		process.on(sig, () => {
			shuttingDown = true;
			note(`${sig} received: shutting omp down, no relaunch`);
			relaunch = undefined;
			child.kill(sig);
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
	}
}
