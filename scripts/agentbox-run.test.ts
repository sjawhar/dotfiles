import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { movedRefusal, OOM_BIAS_ADJ, oomBiasPass, relaunchArgv } from "./agentbox-run.ts";

const resumedSessionId = "NEW";

describe("relaunchArgv", () => {
	test.each([
		{
			name: "drops the original resume selector and launch prompt, and adds none",
			argv: ["omp", "--resume", "OLD", "Your session was restarted in a new agentbox. ..."],
			expected: ["omp", "--resume", resumedSessionId],
		},
		{
			name: "keeps configuration flags and their values",
			argv: ["omp", "--model", "x", "--append-system-prompt", "text"],
			expected: ["omp", "--model", "x", "--append-system-prompt", "text", "--resume", resumedSessionId],
		},
		{
			name: "drops inline resume and continuation selectors",
			argv: ["omp", "--resume=OLD", "-c", "--continue"],
			expected: ["omp", "--resume", resumedSessionId],
		},
		{
			name: "drops the end-of-options delimiter and literal prompt text",
			argv: ["omp", "--model", "x", "--", "fix the bug"],
			expected: ["omp", "--model", "x", "--resume", resumedSessionId],
		},
		{
			name: "drops a bare positional prompt",
			argv: ["omp", "fix the bug"],
			expected: ["omp", "--resume", resumedSessionId],
		},
		{
			name: "keeps an unknown extension flag and its value",
			argv: ["omp", "--ext-flag", "value"],
			expected: ["omp", "--ext-flag", "value", "--resume", resumedSessionId],
		},
	])("$name", ({ argv, expected }) => {
		expect(relaunchArgv(argv, resumedSessionId)).toEqual(expected);
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
			env: { ...process.env, HOME: home, AGENTBOX_HOST: "oldbox", AGENTBOX_BOX: "agentbox-test", PATH: `${bin}:${process.env.PATH}` },
		});
		expect(run.exitCode).toBe(3);
		expect(run.stderr.toString()).toContain("this session moved to sami-devbox");
		expect(fs.existsSync(path.join(home, "OMP-RAN"))).toBe(false);
		expect(fs.readFileSync(path.join(home, ".cache/omp/agentbox/agentbox-test.log"), "utf8")).toContain("refused:");
	});
});
