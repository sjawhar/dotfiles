import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { OOM_BIAS_ADJ, oomBiasPass, relaunchArgv } from "./agentbox-run.ts";

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
