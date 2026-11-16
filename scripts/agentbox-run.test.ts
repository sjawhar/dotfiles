import { afterEach, describe, expect, test } from "bun:test";
import type { Subprocess } from "bun";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { dispatchEnv, movedRefusal, OOM_BIAS_ADJ, oomBiasPass, pendingRequests, relaunchArgv } from "./agentbox-run.ts";

// A restart resumes the session's transcript by its path (dispatch://INFRA-1720).
const resumed = "/home/u/.omp/agent/sessions/-x/2026-11-15T00-00-00-000Z_01a1e000-0000-7000-8000-000000000001.jsonl";

describe("relaunchArgv", () => {
	test.each([
		{
			name: "drops the original resume selector and launch prompt, and adds none",
			argv: ["omp", "--resume", "OLD", "Your session was restarted in a new agentbox. ..."],
			expected: ["omp", "--resume", resumed],
		},
		{
			name: "keeps configuration flags and their values",
			argv: ["omp", "--model", "x", "--append-system-prompt", "text"],
			expected: ["omp", "--model", "x", "--append-system-prompt", "text", "--resume", resumed],
		},
		{
			name: "drops inline resume and continuation selectors",
			argv: ["omp", "--resume=OLD", "-c", "--continue"],
			expected: ["omp", "--resume", resumed],
		},
		{
			name: "drops the end-of-options delimiter and literal prompt text",
			argv: ["omp", "--model", "x", "--", "fix the bug"],
			expected: ["omp", "--model", "x", "--resume", resumed],
		},
		{
			name: "drops a bare positional prompt",
			argv: ["omp", "fix the bug"],
			expected: ["omp", "--resume", resumed],
		},
		{
			name: "keeps an unknown extension flag and its value",
			argv: ["omp", "--ext-flag", "value"],
			expected: ["omp", "--ext-flag", "value", "--resume", resumed],
		},
	])("$name", ({ argv, expected }) => {
		expect(relaunchArgv(argv, resumed)).toEqual(expected);
	});
});

// A fake /proc: each process has an oom_score_adj and, per thread, the children that thread forked.
function fakeProc(procs: Record<number, { adj: number; threads: Record<number, number[]> }>): string {
	const root = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-run-proc-"));
	for (const [pid, { adj, threads }] of Object.entries(procs)) {
		fs.mkdirSync(path.join(root, pid, "task"), { recursive: true });
		fs.writeFileSync(path.join(root, pid, "oom_score_adj"), `${adj}\n`);
		for (const [tid, kids] of Object.entries(threads)) {
			fs.mkdirSync(path.join(root, pid, "task", tid));
			fs.writeFileSync(path.join(root, pid, "task", tid, "children"), kids.map((k) => `${k} `).join(""));
		}
	}
	return root;
}

const adjOf = (root: string, pid: number) => Number(fs.readFileSync(path.join(root, String(pid), "oom_score_adj"), "utf8"));

describe("oomBiasPass", () => {
	const runner = 10;
	const omp = 20;
	const tree = () =>
		fakeProc({
			[runner]: { adj: 0, threads: { 10: [omp, 30] } }, // 30: the box's dockerd
			[omp]: { adj: 0, threads: { 20: [40], 21: [41] } }, // 41 was forked by another omp thread
			30: { adj: 0, threads: { 30: [] } },
			40: { adj: 0, threads: { 40: [50] } }, // 50: forked before the first pass
			41: { adj: 0, threads: { 41: [] } },
			50: { adj: 0, threads: { 50: [] } },
		});

	test("raises every box process but the runner and omp, including children of other threads", () => {
		const root = tree();
		expect(oomBiasPass(runner, omp, OOM_BIAS_ADJ, root)).toBe(4);
		expect([30, 40, 41, 50].map((pid) => adjOf(root, pid))).toEqual([500, 500, 500, 500]);
		expect([runner, omp].map((pid) => adjOf(root, pid))).toEqual([0, 0]);
	});

	test("never lowers an adj, and a second pass changes nothing", () => {
		const root = tree();
		fs.writeFileSync(path.join(root, "40", "oom_score_adj"), "800\n");
		oomBiasPass(runner, omp, OOM_BIAS_ADJ, root);
		expect(adjOf(root, 40)).toBe(800);
		expect(oomBiasPass(runner, omp, OOM_BIAS_ADJ, root)).toBe(0);
	});
});

describe("pendingRequests", () => {
	test("oldest first, answered ones and names that are no id left out, and a stale one deleted unanswered", () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-requests-"));
		const now = Date.now();
		for (const [name, age] of [["b-2", 1_000], ["a-1", 2_000], ["c-3", 0], ["stale-0", 120_000], ["no id", 0]] as const) {
			fs.writeFileSync(path.join(dir, name), "");
			fs.utimesSync(path.join(dir, name), (now - age) / 1000, (now - age) / 1000);
		}
		expect(pendingRequests(dir, new Set(["c-3"]), now)).toEqual(["a-1", "b-2"]);
		expect(fs.existsSync(path.join(dir, "stale-0"))).toBe(false);
		expect(pendingRequests(path.join(dir, "absent"))).toEqual([]);
		fs.rmSync(dir, { recursive: true });
	});
});

describe("movedRefusal", () => {
	const sid = "01a19700-0000-7000-8000-00000000c001";
	// A home with one session `agentbox move --to` moved from `oldbox` to `sami-devbox`: the
	// marker in the session's own directory and its copy in the id index.
	function moved(from = "oldbox"): { home: string; transcript: string } {
		const home = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-moved-"));
		const transcript = path.join(home, ".omp/agent/sessions/-lane", `2026-11-02T10-00-00-000Z_${sid}.jsonl`);
		const marker = `from=${from}\nto=sami-devbox\ntime=2026-11-02T12:00:00Z\nsession=${sid}\ntranscript=${transcript}\n`;
		fs.mkdirSync(transcript.slice(0, -".jsonl".length), { recursive: true });
		fs.writeFileSync(`${transcript.slice(0, -".jsonl".length)}/agentbox-moved`, marker);
		fs.mkdirSync(path.join(home, ".omp/agentbox-moved"), { recursive: true });
		fs.writeFileSync(path.join(home, ".omp/agentbox-moved", sid), marker);
		return { home, transcript };
	}

	test("refuses a resume of a moved session by its transcript path, its id and a prefix of it", () => {
		const { home, transcript } = moved();
		for (const argv of [
			["omp", "--resume", transcript],
			["omp", `--resume=${transcript}`],
			["omp", "-r", sid],
			["omp", "--session", sid.slice(0, 13)],
		]) {
			expect(movedRefusal(argv, home, "oldbox")).toContain("moved to sami-devbox at 2026-11-02T12:00:00Z");
		}
	});

	test("lets the launch go ahead on another machine, for another session and with no resume", () => {
		const { home, transcript } = moved();
		expect(movedRefusal(["omp", "--resume", transcript], home, "sami-devbox")).toBeUndefined();
		expect(movedRefusal(["omp", "--resume", "01a19700-0000-7000-8000-00000000c002"], home, "oldbox")).toBeUndefined();
		expect(movedRefusal(["omp", "--model", "x"], home, "oldbox")).toBeUndefined();
		expect(movedRefusal(["omp", "--", "--resume", sid], home, "oldbox")).toBeUndefined();
	});

	test("a marker another machine wrote, copied here, is inert", () => {
		const { home, transcript } = moved("sami-leanperf");
		expect(movedRefusal(["omp", "--resume", transcript], home, "oldbox")).toBeUndefined();
	});

	test("the runner refuses to start a moved session, says why in the box's log, and starts no omp", () => {
		const { home, transcript } = moved();
		// An `omp` first on PATH that leaves a trace: it must never run.
		const bin = path.join(home, "bin");
		fs.mkdirSync(bin);
		fs.writeFileSync(path.join(bin, "omp"), `#!/bin/sh\ntouch "${home}/OMP-RAN"\n`, { mode: 0o755 });
		const run = Bun.spawnSync(["bun", path.join(import.meta.dir, "agentbox-run.ts"), "omp", "--resume", transcript], {
			// A runtime directory of its own: the real one is the host's.
			env: { ...process.env, HOME: home, XDG_RUNTIME_DIR: home, AGENTBOX_HOST: "oldbox", AGENTBOX_BOX: "agentbox-test", PATH: `${bin}:${process.env.PATH}` },
		});
		expect(run.exitCode).toBe(3);
		expect(run.stderr.toString()).toContain("this session moved to sami-devbox");
		expect(fs.existsSync(path.join(home, "OMP-RAN"))).toBe(false);
		expect(fs.readFileSync(path.join(home, ".cache/omp/agentbox/agentbox-test.log"), "utf8")).toContain("refused:");
	});
});

describe("dispatchEnv", () => {
	const sid = "01a19e2e-0000-7000-8000-00000000a001";
	function home(): { home: string; bin: string; envoy: string } {
		const h = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-dispatch-"));
		const bin = path.join(h, ".dotfiles/omp/plugins/node_modules/@sjawhar/pi-envoy/bin");
		fs.mkdirSync(bin, { recursive: true });
		fs.writeFileSync(path.join(bin, "dispatch"), "#!/bin/sh\n", { mode: 0o755 });
		const envoy = path.join(h, ".config/legion/envoy.json");
		fs.mkdirSync(path.dirname(envoy), { recursive: true });
		fs.writeFileSync(envoy, "{}");
		return { home: h, bin, envoy };
	}

	test("a box's omp gets the host session's dispatch environment (BET-561, 2026-11-03)", () => {
		const { home: h, bin, envoy } = home();
		const transcript = `${h}/.omp/agent/sessions/-x/2026-11-03T00-00-00-000Z_${sid}.jsonl`;
		const env = dispatchEnv(["omp", "--resume", transcript], { HOME: h, PATH: "/usr/bin:/bin" });
		expect(env).toEqual({ DISPATCH_HOST: "omp", ENVOY_CONFIG_FILE: envoy, DISPATCH_SESSION_ID: sid, PATH: `${bin}:/usr/bin:/bin` });
		expect(dispatchEnv(["omp", "-r", sid], { HOME: h, PATH: "/bin" }).DISPATCH_SESSION_ID).toBe(sid);
	});

	test("a value already set wins, and nothing is invented", () => {
		const { home: h, bin } = home();
		const env = dispatchEnv(["omp"], {
			HOME: h, PATH: `${bin}:/bin`, DISPATCH_HOST: "claude", ENVOY_CONFIG_FILE: "/x.json", DISPATCH_SESSION_ID: "set",
		});
		expect(env).toEqual({});
		const bare = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-dispatch-none-"));
		expect(dispatchEnv(["omp"], { HOME: bare, PATH: "/bin" })).toEqual({ DISPATCH_HOST: "omp" });
	});
});

// The runner itself, run as a process in a scratch home and runtime directory, with an `omp` first
// on PATH that records its argv and pid, keeps running until it is hung up, and records on SIGHUP
// what the box held then; and an `agentbox` that records the waker's argv.
const RUNNER = path.join(import.meta.dir, "agentbox-run.ts");
const SID = "01a1e000-0000-7000-8000-0000000000e1";
const FAKE_OMP = `#!/bin/sh
trap ': >"$REC/hup.$$"; [ -p "$XDG_RUNTIME_DIR/agentbox-restart" ] || cat "$XDG_RUNTIME_DIR/agentbox-restart" >"$REC/answers-at-hup.$$" 2>/dev/null; ls "$HOME/.cache/omp/agentbox" >"$REC/markers-at-hup.$$" 2>/dev/null; sleep "\${FAKE_OMP_HUP_DELAY:-0}"; kill "$sleeper" 2>/dev/null; exit 0' HUP
trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
[ -n "$FAKE_OMP_HOLD" ] && exec 3>>"$FAKE_OMP_HOLD"
cat "$XDG_RUNTIME_DIR/agentbox-runner" >"$REC/runner-at-start.$$" 2>/dev/null
printf '%s\\0' "$@" >"$REC/argv.$$"
echo $$ >>"$REC/pids"
sleep 3600 &
sleeper=$!
wait "$sleeper"
`;
const FAKE_AGENTBOX = `#!/bin/sh
printf '%s\\0' "$@" >"$REC/agentbox-argv.tmp" && mv "$REC/agentbox-argv.tmp" "$REC/agentbox-argv"
`;

type Box = { home: string; runtime: string; rec: string; runner: Subprocess };
const boxes: Box[] = [];
afterEach(async () => {
	for (const box of boxes.splice(0)) {
		box.runner.kill("SIGTERM"); // forwarded to the omp it runs
		await Promise.race([box.runner.exited, Bun.sleep(5_000)]);
		box.runner.kill("SIGKILL");
		for (const pid of ompPids(box)) {
			try {
				process.kill(pid, "SIGTERM");
			} catch {}
		}
		fs.rmSync(box.home, { recursive: true, force: true });
	}
});

// Another process's progress shows only in files, so these waits poll them.
async function until(what: string, ready: () => boolean): Promise<void> {
	for (const deadline = Date.now() + 10_000; !ready(); await Bun.sleep(20)) {
		if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
	}
}
function ompPids(box: Box): number[] {
	try {
		return fs.readFileSync(path.join(box.rec, "pids"), "utf8").split("\n").filter(Boolean).map(Number);
	} catch {
		return [];
	}
}
const recorded = (box: Box, name: string) => fs.readFileSync(path.join(box.rec, name), "utf8");
const words = (text: string) => text.split("\0").slice(0, -1);
/** The answer file's lines, as `<word> <text>`, each checked for its ISO-8601 UTC time first. */
function answersIn(text: string): string[] {
	return text
		.split("\n")
		.filter(Boolean)
		.map((line) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z (\S+ .*)$/.exec(line)?.[1] ?? `MALFORMED ${line}`);
}
function answers(box: Box): string[] {
	try {
		return answersIn(fs.readFileSync(path.join(box.runtime, "agentbox-restart"), "utf8"));
	} catch {
		return [];
	}
}

/** A session's transcript under `home`, created empty. */
function transcript(home: string, dir = "-work"): string {
	const file = path.join(home, ".omp/agent/sessions", dir, `2026-11-15T10-00-00-000Z_${SID}.jsonl`);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, "");
	return file;
}
/** A runner started on `launch`, once its omp runs. `prepare` sets up the home and returns env. */
async function startBox(launch: string[], prepare: (home: string) => Record<string, string> = () => ({})): Promise<Box> {
	const home = fs.mkdtempSync(path.join(os.tmpdir(), "agentbox-run-box-"));
	const runtime = path.join(home, "run");
	const rec = path.join(home, "rec");
	const bin = path.join(home, "bin");
	for (const dir of [runtime, rec, bin]) fs.mkdirSync(dir);
	fs.writeFileSync(path.join(bin, "omp"), FAKE_OMP, { mode: 0o755 });
	fs.writeFileSync(path.join(bin, "agentbox"), FAKE_AGENTBOX, { mode: 0o755 });
	const env: Record<string, string | undefined> = {
		...process.env,
		HOME: home,
		XDG_RUNTIME_DIR: runtime,
		REC: rec,
		AGENTBOX_BOX: "agentbox-test",
		AGENTBOX_HOST: "testhost",
		PATH: `${bin}:${process.env.PATH}`,
		...prepare(home),
	};
	delete env.AGENTBOX_RUNNER_PID;
	const runner = Bun.spawn([process.execPath, RUNNER, ...launch], { env, stdin: "ignore", stdout: "ignore", stderr: "ignore" });
	const box = { home, runtime, rec, runner };
	boxes.push(box);
	await until("the first omp", () => ompPids(box).length === 1);
	return box;
}
/** What session-env writes in the omp of a box. */
function report(box: Box, pid: number, file: string, sid = SID): void {
	fs.writeFileSync(path.join(box.runtime, "agentbox-session"), `version=1\npid=${pid}\nsession=${sid}\nfile=${file}\n`);
}
/**
 * The signals the kernel holds for PID, delivered to none of its threads yet: `SigPnd` (this
 * thread's) and `ShdPnd` (the process's) of /proc/<pid>/status, as one mask whose bit N-1 is
 * signal N. A signal that was sent and not yet handled is in it, so a check of "it has not been
 * signalled" needs no wait.
 */
function pendingSignals(pid: number): bigint {
	let mask = 0n;
	for (const line of fs.readFileSync(`/proc/${pid}/status`, "utf8").split("\n")) {
		const hex = /^(?:SigPnd|ShdPnd):\s+([0-9a-f]+)$/.exec(line)?.[1];
		if (hex) mask |= BigInt(`0x${hex}`);
	}
	return mask;
}
/**
 * The first omp got no SIGHUP so far: a good report now restarts it, and the one SIGHUP it gets
 * finds every answer before it already in the file, the last one `started`.
 */
async function expectOmpUntouched(box: Box): Promise<void> {
	const [omp] = ompPids(box);
	expect(ompPids(box)).toEqual([omp]);
	const before = answers(box);
	const file = transcript(box.home);
	report(box, omp, file);
	box.runner.kill("SIGUSR1");
	await until("a restart after the refusal", () => ompPids(box).length === 2);
	expect(answersIn(recorded(box, `answers-at-hup.${omp}`))).toEqual([
		...before,
		`started session ${SID}: hanging up omp (pid ${omp}) to resume ${file}`,
	]);
}

describe("the runner's restart", () => {
	test("restartResumesTheReportedSession: launch flags and --resume <file>, the id to the marker and the waker, one started line", async () => {
		const box = await startBox(["omp", "--model", "x", "--append-system-prompt", "be brief", "start on the bug"]);
		const [omp] = ompPids(box);
		const file = transcript(box.home);
		report(box, omp, file);
		box.runner.kill("SIGUSR1");
		await until("the second omp", () => ompPids(box).length === 2);
		expect(words(recorded(box, `argv.${ompPids(box)[1]}`))).toEqual(["--model", "x", "--append-system-prompt", "be brief", "--resume", file]);
		expect(recorded(box, `markers-at-hup.${omp}`).split("\n")).toContain(`restarting-${SID}`);
		await until("the waker", () => fs.existsSync(path.join(box.rec, "agentbox-argv")));
		expect(words(recorded(box, "agentbox-argv")).slice(0, 3)).toEqual(["move", "--wake", SID]);
		expect(answers(box)).toEqual([`started session ${SID}: hanging up omp (pid ${omp}) to resume ${file}`]);
		expect(box.runner.exitCode).toBeNull();
	}, 20_000);

	test.each([
		["is missing", (dir: string) => path.join(dir, "gone.jsonl")],
		[
			"is a dangling symlink",
			(dir: string) => {
				fs.symlinkSync(path.join(dir, "nowhere.jsonl"), path.join(dir, "dangling.jsonl"));
				return path.join(dir, "dangling.jsonl");
			},
		],
		[
			"is a directory",
			(dir: string) => {
				fs.mkdirSync(path.join(dir, "a-directory.jsonl"));
				return path.join(dir, "a-directory.jsonl");
			},
		],
	])("restartRefusesAGonePathBeforeStoppingOmp: a recorded file that %s is refused, and omp is left running", async (_name, make) => {
		const box = await startBox(["omp"]);
		const gone = make(box.home);
		report(box, ompPids(box)[0], gone);
		box.runner.kill("SIGUSR1");
		await until("the answer", () => answers(box).length === 1);
		expect(answers(box)).toEqual([expect.stringMatching(/^refused /)]);
		expect(answers(box)[0]).toContain(`${gone} is not a regular file`);
		await expectOmpUntouched(box);
	}, 20_000);

	test("a session moved off this machine is refused with the move's reason, and omp is left running", async () => {
		const box = await startBox(["omp"]);
		const file = transcript(box.home);
		const sessionDir = file.slice(0, -".jsonl".length);
		fs.mkdirSync(sessionDir);
		fs.writeFileSync(`${sessionDir}/agentbox-moved`, `from=testhost\nto=sami-devbox\ntime=2026-11-15T12:00:00Z\nsession=${SID}\n`);
		report(box, ompPids(box)[0], file);
		box.runner.kill("SIGUSR1");
		await until("the answer", () => answers(box).length === 1);
		expect(answers(box)).toEqual([expect.stringMatching(/^refused this session moved to sami-devbox at 2026-11-15T12:00:00Z/)]);
		fs.rmSync(sessionDir, { recursive: true });
		await expectOmpUntouched(box);
	}, 20_000);
});

describe("restartAnswersInTheSignalHandler", () => {
	test("the version file names the runner before omp starts", async () => {
		const box = await startBox(["omp"]);
		const version = `version=1\npid=${box.runner.pid}\n`;
		expect(recorded(box, `runner-at-start.${ompPids(box)[0]}`)).toBe(version);
		expect(fs.readFileSync(path.join(box.runtime, "agentbox-runner"), "utf8")).toBe(version);
	}, 20_000);

	// An answer file nobody reads yet (a FIFO) holds the runner in its append: with the line in
	// the handler before the hang-up, the runner cannot reach the hang-up until this test reads
	// the FIFO. The barrier is the runner's own log line, which `answer` writes as it starts, and
	// the kernel's pending-signal mask for omp, so a hang-up that was sent but not yet handled is
	// seen too (`SigPnd`/`ShdPnd` in /proc/<pid>/status, bit 0 for SIGHUP). Nothing here waits on
	// a clock.
	test("the started line is written before omp is hung up", async () => {
		const box = await startBox(["omp"]);
		const [omp] = ompPids(box);
		const file = transcript(box.home);
		report(box, omp, file);
		const fifo = path.join(box.runtime, "agentbox-restart");
		expect(Bun.spawnSync(["mkfifo", fifo]).exitCode).toBe(0);
		box.runner.kill("SIGUSR1");
		// The runner notes each answer before it writes it, so it is now in that write.
		const log = path.join(box.home, ".cache/omp/agentbox", "agentbox-test.log");
		await until("the runner's note of the answer", () => fs.readFileSync(log, "utf8").includes("restart request: started"));
		expect(pendingSignals(omp) & 1n).toBe(0n);
		expect(fs.existsSync(path.join(box.rec, `hup.${omp}`))).toBe(false);
		expect(answersIn(fs.readFileSync(fifo, "utf8"))).toEqual([`started session ${SID}: hanging up omp (pid ${omp}) to resume ${file}`]);
		await until("the hang-up", () => fs.existsSync(path.join(box.rec, `hup.${omp}`)));
		await until("the second omp", () => ompPids(box).length === 2);
	}, 20_000);

	test.each([
		["no session file", (_box: Box) => {}],
		["a report of an earlier omp in this box", (box: Box) => report(box, ompPids(box)[0] + 100_000, transcript(box.home))],
		["a report with no session id", (box: Box) => report(box, ompPids(box)[0], transcript(box.home), "")],
		["a report of a relative path", (box: Box) => report(box, ompPids(box)[0], "relative.jsonl")],
		[
			"a report of another version",
			(box: Box) =>
				fs.writeFileSync(path.join(box.runtime, "agentbox-session"), `version=2\npid=${ompPids(box)[0]}\nsession=${SID}\nfile=${transcript(box.home)}\n`),
		],
	])("%s is refused because the session did not report its id, and omp is left running", async (_name, setUp) => {
		const box = await startBox(["omp"]);
		setUp(box);
		box.runner.kill("SIGUSR1");
		await until("the answer", () => answers(box).length === 1);
		expect(answers(box)).toEqual([expect.stringMatching(/^refused .*the session did not report its id/)]);
		await expectOmpUntouched(box);
	}, 20_000);

	test("a second request while a restart is in progress gets one busy line and no second restart", async () => {
		// The first omp takes a second to go, so the second request lands in the middle of the restart.
		const box = await startBox(["omp"], () => ({ FAKE_OMP_HUP_DELAY: "1" }));
		const [omp] = ompPids(box);
		const file = transcript(box.home);
		report(box, omp, file);
		box.runner.kill("SIGUSR1");
		await until("the started line", () => answers(box).length === 1);
		box.runner.kill("SIGUSR1");
		await until("the second answer", () => answers(box).length === 2);
		expect(answers(box)).toEqual([
			`started session ${SID}: hanging up omp (pid ${omp}) to resume ${file}`,
			"busy a restart is already in progress; this signal is ignored",
		]);
		await until("the second omp", () => ompPids(box).length === 2);
		await until("the waker", () => fs.existsSync(path.join(box.rec, "agentbox-argv")));
		expect(ompPids(box)).toHaveLength(2);
		expect(answers(box)).toHaveLength(2);
	}, 20_000);

	test("each request is answered under its own id: of two at once the second is busy, and so is one during the restart", async () => {
		// Two callers that each left a request before one signal reached the runner (two signals can
		// arrive as one), then a third that signals while the restart runs. The runner hangs omp up a
		// second after it is asked, so the third lands in the middle of the restart.
		const box = await startBox(["omp"], () => ({ FAKE_OMP_HUP_DELAY: "1" }));
		const [omp] = ompPids(box);
		const file = transcript(box.home);
		report(box, omp, file);
		const requests = path.join(box.runtime, "agentbox-restart.d");
		fs.mkdirSync(requests);
		for (const id of ["20261116T100000000000000-1-1", "20261116T100000000000001-2-2"]) fs.writeFileSync(path.join(requests, id), "");
		box.runner.kill("SIGUSR1");
		await until("both answers", () => answers(box).length === 2);
		fs.writeFileSync(path.join(requests, "20261116T100000000000002-3-3"), "");
		box.runner.kill("SIGUSR1");
		await until("the third answer", () => answers(box).length === 3);
		expect(answers(box)).toEqual([
			`started session ${SID}: hanging up omp (pid ${omp}) to resume ${file} [request 20261116T100000000000000-1-1]`,
			"busy another restart request came first; this one is ignored [request 20261116T100000000000001-2-2]",
			"busy a restart is already in progress; this signal is ignored [request 20261116T100000000000002-3-3]",
		]);
		expect(fs.readdirSync(requests)).toEqual([]);
		await until("the second omp", () => ompPids(box).length === 2);
		await until("the waker", () => fs.existsSync(path.join(box.rec, "agentbox-argv")));
		expect(ompPids(box)).toHaveLength(2);
	}, 20_000);

	// The routes the runner took before the session reported itself, each set up so that it alone
	// would have found this session. With no report, every one is refused.
	test.each([
		["the transcript omp holds open", ["omp"], (home: string) => ({ FAKE_OMP_HOLD: transcript(home) })],
		[
			"the newest transcript in a directory named after the box",
			["omp"],
			(home: string) => {
				transcript(home, "-boxes-agentbox-test-repo");
				return {};
			},
		],
		[
			"the runner's own --resume flag",
			["omp", "--resume", SID],
			(home: string) => {
				transcript(home);
				return {};
			},
		],
	])("with no session file, %s is never taken for the session", async (_name, launch, prepare) => {
		const box = await startBox(launch, prepare);
		box.runner.kill("SIGUSR1");
		await until("the answer", () => answers(box).length === 1);
		expect(answers(box)).toEqual([expect.stringMatching(/^refused .*the session did not report its id/)]);
		await expectOmpUntouched(box);
	}, 20_000);
});
