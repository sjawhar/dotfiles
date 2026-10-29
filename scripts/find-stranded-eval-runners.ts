#!/usr/bin/env bun
/**
 * List Python eval runners on this Linux host that no live kernel will use again.
 *
 * Read-only: it reads `/proc` and prints. It signals nothing and kills nothing.
 * Linux only — it needs `/proc/<pid>/{stat,status,environ,cwd}`.
 *
 * Two classes are reported.
 *
 *   orphan    the process that spawned the runner is gone: its parent is now
 *             pid 1 or a subreaper that started after it, or `/proc/<ppid>` has
 *             no stat at all. The runner polls `getppid()` every 10s and exits
 *             when it is reparented, so a persistent orphan means that poll
 *             never ran.
 *
 *   stranded  the spawning parent is alive, and another runner under that same
 *             parent serves the same agent session. A retained kernel is keyed
 *             by (eval session id, cwd, interpreter) and the eval session id
 *             ends in `:cwd:<cwd>`, so one agent session has at most one kernel
 *             its next cell will use. Every other runner in that group is heap
 *             the host will not reclaim before it exits.
 *
 * What does NOT work, measured rather than assumed: the control channel between
 * the host and a runner it has walked away from stays open at both ends — `ss
 * -x` shows the host still holding all three stdio sockets of a stranded runner
 * — so no `lsof` or `/proc/<pid>/fd` comparison separates a stranded runner
 * from a live one. Neither does idleness: a legitimately retained kernel is
 * idle and fat too. The grouping above is the signal; RSS and CPU only rank it.
 *
 * False positives, stated plainly because output invites a `kill`:
 *
 *   - A stranded group of N runners means N-1 are unreclaimable. That part is
 *     exact. An orphan is unreclaimable outright.
 *   - WHICH member of a stranded group is still reachable cannot be read from
 *     outside the host process. This script assumes the newest-started member
 *     is the live one. An agent that returns to a directory it used earlier
 *     makes an older member live again and the newest one stranded, so a `kill`
 *     driven from this output can end one kernel a session could still have
 *     used.
 *   - Nothing here can tell you whether a runner is executing a cell right now:
 *     a cell runs on the runner's main thread and writes no file, so neither
 *     the thread count nor the fd table changes. Total CPU is printed as a weak
 *     hint only. Before killing a runner of a live host, confirm with whoever
 *     owns that session.
 *   - Runners with no `PI_SESSION_FILE` (the host had not yet written the
 *     session to disk) are grouped per parent as `<unsaved session>`; unrelated
 *     SDK sessions in one process can land in one such group, so those groups
 *     are never marked stranded.
 *   - A runner inside its reclaim grace is reported as stranded although the
 *     host is about to end it. When a session moves to another key, the host
 *     keeps the kernel it left for a short grace (two minutes) so a return
 *     inside that window is a cache hit that keeps the namespace; only after
 *     the grace does the host shut it down. A scan that lands in that window
 *     sees a stranded group that resolves itself. Re-scan after the grace
 *     before acting on such a finding.
 *
 * Usage: find-stranded-eval-runners.ts [--json] [--all]
 */
import * as fs from "node:fs";
import * as path from "node:path";

const RUNNER_MARKER = "omp-python-runner";
const UNSAVED_SESSION = "<unsaved session>";
const CLOCK_TICKS_PER_SECOND = 100;

interface RunnerProcess {
	pid: number;
	ppid: number;
	/** Resident set size in bytes, read from `VmRSS` so the page size never matters. */
	rss: number;
	/** Seconds of user+system CPU the runner has burned. */
	cpuSeconds: number;
	/** Seconds since the runner started. */
	ageSeconds: number;
	/** Start time in clock ticks since boot; the tiebreak for "newest". */
	startTicks: number;
	cwd: string;
	sessionFile: string;
	evalSessionId: string;
	parentCommand: string;
	/** The spawning parent is still alive: not pid 1, present, and older than the runner. */
	parentAlive: boolean;
}

interface RunnerGroup {
	ppid: number;
	sessionFile: string;
	runners: RunnerProcess[];
}

interface Finding {
	kind: "orphan" | "stranded";
	runner: RunnerProcess;
	/** The runner this script believes the session is using, for stranded findings. */
	liveSibling?: number;
}

function readText(file: string): string | undefined {
	try {
		return fs.readFileSync(file, "utf8");
	} catch {
		return undefined;
	}
}

function readEnviron(pid: number): Record<string, string> {
	const raw = readText(`/proc/${pid}/environ`);
	if (raw === undefined) return {};
	const env: Record<string, string> = {};
	for (const entry of raw.split("\0")) {
		const eq = entry.indexOf("=");
		if (eq > 0) env[entry.slice(0, eq)] = entry.slice(eq + 1);
	}
	return env;
}

/** `/proc/<pid>/stat` after the comm field, which may itself contain spaces and parentheses. */
function readStatFields(pid: number): string[] | undefined {
	const raw = readText(`/proc/${pid}/stat`);
	if (raw === undefined) return undefined;
	const close = raw.lastIndexOf(")");
	if (close < 0) return undefined;
	return raw.slice(close + 2).split(" ");
}

/** Resident bytes from `VmRSS`, which is already in kB whatever the kernel's page size is. */
function readResidentBytes(pid: number): number {
	const status = readText(`/proc/${pid}/status`);
	const match = status === undefined ? null : /^VmRSS:\s+(\d+) kB$/m.exec(status);
	return match ? Number(match[1]) * 1024 : 0;
}

function collectRunners(): RunnerProcess[] {
	const uptimeSeconds = Number((readText("/proc/uptime") ?? "0").split(" ")[0]);
	const runners: RunnerProcess[] = [];
	for (const entry of fs.readdirSync("/proc")) {
		if (!/^\d+$/.test(entry)) continue;
		const pid = Number(entry);
		const cmdline = readText(`/proc/${pid}/cmdline`);
		if (cmdline === undefined || !cmdline.includes(RUNNER_MARKER)) continue;
		const fields = readStatFields(pid);
		if (fields === undefined || fields[0] === "Z") continue;
		const ppid = Number(fields[1]);
		const parentFields = ppid > 1 ? readStatFields(ppid) : undefined;
		const startTicks = Number(fields[19]);
		const env = readEnviron(pid);
		let cwd = "<unreadable>";
		try {
			cwd = fs.readlinkSync(`/proc/${pid}/cwd`);
		} catch {
			/* a runner whose cwd was deleted, or one we may not follow */
		}
		runners.push({
			pid,
			ppid,
			rss: readResidentBytes(pid),
			cpuSeconds: (Number(fields[11]) + Number(fields[12])) / CLOCK_TICKS_PER_SECOND,
			ageSeconds: Math.max(0, uptimeSeconds - startTicks / CLOCK_TICKS_PER_SECOND),
			startTicks,
			cwd,
			sessionFile: env.PI_SESSION_FILE ?? UNSAVED_SESSION,
			evalSessionId: env.PI_TOOL_BRIDGE_SESSION ?? "<unknown>",
			parentCommand: (readText(`/proc/${ppid}/cmdline`) ?? "").replaceAll("\0", " ").trim(),
			// pid 1 and a subreaper that outlived the spawner both mean reparented.
			parentAlive: parentFields !== undefined && Number(parentFields[19]) <= startTicks,
		});
	}
	return runners;
}

function findLeaks(runners: readonly RunnerProcess[]): Finding[] {
	const findings: Finding[] = [];
	const groups = new Map<string, RunnerGroup>();
	for (const runner of runners) {
		if (!runner.parentAlive) {
			findings.push({ kind: "orphan", runner });
			continue;
		}
		if (runner.sessionFile === UNSAVED_SESSION) continue;
		const key = `${runner.ppid}\0${runner.sessionFile}`;
		const group = groups.get(key);
		if (group) group.runners.push(runner);
		else groups.set(key, { ppid: runner.ppid, sessionFile: runner.sessionFile, runners: [runner] });
	}
	for (const group of groups.values()) {
		if (group.runners.length < 2) continue;
		group.runners.sort((a, b) => a.startTicks - b.startTicks);
		const live = group.runners[group.runners.length - 1];
		for (const runner of group.runners.slice(0, -1)) {
			findings.push({ kind: "stranded", runner, liveSibling: live.pid });
		}
	}
	return findings;
}

function formatBytes(bytes: number): string {
	if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)}G`;
	return `${Math.round(bytes / 1024 ** 2)}M`;
}

function formatDuration(seconds: number): string {
	if (seconds >= 86_400) return `${Math.floor(seconds / 86_400)}d${Math.floor((seconds % 86_400) / 3600)}h`;
	if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h${Math.floor((seconds % 3600) / 60)}m`;
	return `${Math.floor(seconds / 60)}m`;
}

if (process.platform !== "linux") {
	console.error("This script reads /proc and only runs on Linux.");
	process.exit(2);
}

const args = new Set(Bun.argv.slice(2));
const runners = collectRunners();
const findings = findLeaks(runners).sort((a, b) => b.runner.rss - a.runner.rss);

if (args.has("--json")) {
	console.log(JSON.stringify({ scanned: runners.length, findings }, null, 2));
	process.exit(0);
}

const reclaimable = findings.reduce((total, finding) => total + finding.runner.rss, 0);
console.log(
	`scanned ${runners.length} runner(s); ${findings.length} unreclaimable holding ${formatBytes(reclaimable)}`,
);

if (findings.length > 0) {
	console.log("");
	console.log(
		[
			"kind".padEnd(8),
			"pid".padStart(8),
			"parent".padStart(8),
			"rss".padStart(7),
			"age".padStart(7),
			"cpu".padStart(7),
		]
			.join(" ")
			.concat("  cwd"),
	);
}
for (const finding of findings) {
	const { runner } = finding;
	console.log(
		[
			finding.kind.padEnd(8),
			String(runner.pid).padStart(8),
			String(runner.ppid).padStart(8),
			formatBytes(runner.rss).padStart(7),
			formatDuration(runner.ageSeconds).padStart(7),
			`${runner.cpuSeconds.toFixed(0)}s`.padStart(7),
		]
			.join(" ")
			.concat(`  ${runner.cwd}`),
	);
	if (finding.liveSibling !== undefined) {
		console.log(`${" ".repeat(9)}superseded by pid ${finding.liveSibling} in ${path.basename(runner.sessionFile)}`);
	}
}

if (args.has("--all")) {
	console.log("\nevery runner seen:");
	for (const runner of [...runners].sort((a, b) => a.ppid - b.ppid || a.startTicks - b.startTicks)) {
		console.log(
			`  parent ${String(runner.ppid).padStart(8)} pid ${String(runner.pid).padStart(8)} ` +
				`${formatBytes(runner.rss).padStart(7)} ${runner.cwd}`,
		);
	}
}

if (findings.length > 0) {
	console.log(
		"\nNothing was signalled. This cannot tell whether a runner is executing a cell; read the false-positive notes at the top of this script before killing anything:",
	);
	console.log(`  kill ${findings.map(finding => finding.runner.pid).join(" ")}`);
}
