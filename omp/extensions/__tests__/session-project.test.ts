import { afterEach, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { recordSessionProject, recordSessionProjectBounded, spendTags } from "../session-project";

const ID = "01a17998-6996-746c-9585-6e829280c54e";
const TOP = `/s/--home-sami--/2026-10-26T19-30-00-000Z_${ID}.jsonl`;
const ENV = {
	OTEL_EXPORTER_OTLP_ENDPOINT: "https://collector.example/otlp",
	OTEL_RESOURCE_ATTRIBUTES: "service.name=x,theorem.project=rocq-speed,theorem.person=sami",
};

const dirs: string[] = [];
function sessions(): string {
	const dir = mkdtempSync(path.join(os.tmpdir(), "session-project-"));
	dirs.push(dir);
	return path.join(dir, "agent-spend", "sessions");
}
afterEach(() => {
	for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("spendTags", () => {
	test("takes the last project and subproject, percent-decoded", () => {
		expect(spendTags("theorem.project=shared,theorem.project=task-making,theorem.subproject=a%2Db")).toEqual({
			project: "task-making",
			subproject: "a-b",
		});
	});
	test("finds nothing in nothing", () => {
		expect(spendTags(undefined)).toEqual({});
	});
});

describe("recordSessionProject", () => {
	test("writes a top-level session's project, private, with no temp file left", async () => {
		const dir = sessions();
		expect(await recordSessionProject(TOP, ENV, dir)).toBe("written");
		expect(readFileSync(path.join(dir, ID), "utf8")).toBe("rocq-speed\n");
		expect(statSync(path.join(dir, ID)).mode & 0o777).toBe(0o600);
		expect(statSync(dir).mode & 0o777).toBe(0o700);
		expect(readdirSync(dir)).toEqual([ID]);
	});

	test("writes the subproject after the project", async () => {
		const dir = sessions();
		const env = { ...ENV, OTEL_RESOURCE_ATTRIBUTES: `${ENV.OTEL_RESOURCE_ATTRIBUTES},theorem.subproject=lane-c` };
		expect(await recordSessionProject(TOP, env, dir)).toBe("written");
		expect(readFileSync(path.join(dir, ID), "utf8")).toBe("rocq-speed lane-c\n");
	});

	test("writes an old project name as its COS stream", async () => {
		const dir = sessions();
		const env = { ...ENV, OTEL_RESOURCE_ATTRIBUTES: "theorem.project=binary-equivalence,theorem.subproject=pipe" };
		expect(await recordSessionProject(TOP, env, dir)).toBe("written");
		expect(readFileSync(path.join(dir, ID), "utf8")).toBe("task-making pipe\n");
	});

	test("never overwrites a session's existing file", async () => {
		const dir = sessions();
		mkdirSync(dir, { recursive: true });
		writeFileSync(path.join(dir, ID), "verified-grader\n");
		expect(await recordSessionProject(TOP, ENV, dir)).toBe("exists");
		expect(readFileSync(path.join(dir, ID), "utf8")).toBe("verified-grader\n");
		expect(readdirSync(dir)).toEqual([ID]);
	});

	test("skips a subagent transcript, a session with no collector, and one with no project", async () => {
		const dir = sessions();
		expect(await recordSessionProject(`/s/2026-10-26T19-30-00-000Z_${ID}/Scout.jsonl`, ENV, dir)).toBe("skipped");
		expect(await recordSessionProject(undefined, ENV, dir)).toBe("skipped");
		expect(await recordSessionProject(TOP, { OTEL_RESOURCE_ATTRIBUTES: ENV.OTEL_RESOURCE_ATTRIBUTES }, dir)).toBe(
			"skipped",
		);
		expect(await recordSessionProject(TOP, { ...ENV, OTEL_RESOURCE_ATTRIBUTES: "theorem.person=sami" }, dir)).toBe(
			"skipped",
		);
		expect(existsSync(dir)).toBe(false);
	});

	test("reports a failure instead of throwing", async () => {
		const parent = sessions();
		mkdirSync(path.dirname(parent), { recursive: true });
		writeFileSync(parent, "a file where the directory should be");
		expect(await recordSessionProjectBounded(TOP, 1_000, ENV, parent)).toBe("failed");
	});
});
