import { describe, expect, test } from "bun:test";
import { intercept } from "../experiments/bash-gate-core";
import { DEFAULT_RULES, OMP } from "./omp-interceptor";

// Every tool a rule of omp's defaults names, so each rule can block.
const TOOLS = ["bash", "read", "grep", "glob", "edit", "write", "hub"];
const run = (command: string, leadingOnly: boolean, extra: Record<string, unknown> = {}, tools = TOOLS) => intercept(OMP, { command, ...extra }, DEFAULT_RULES, tools, leadingOnly);
const ARMS = [
	["on", true],
	["off", false],
] as const;

describe("a read-only rule (read, grep, glob)", () => {
	test("on: blocks only when it matches the leading segment", () => {
		expect(run("ls d && cat f", true)).toBeUndefined();
		expect(run("cmd; grep x f", true)).toBeUndefined();
		expect(run("cat f", true)).toBeDefined();
		expect(run("cat f && ls d", true)).toBeDefined();
	});
	test("off: blocks in any non-piped segment, as omp's interceptor does", () => {
		for (const command of ["ls d && cat f", "cat f", "cat f && ls d", "cmd; grep x f"]) {
			expect(run(command, false)).toBeDefined();
			expect(OMP.check(command, TOOLS, DEFAULT_RULES).block).toBe(true);
		}
	});
	test("on: a leading `cd <path> &&`, which the bash tool turns into the working directory, does not hide the step after it", () => {
		expect(run("cd /tmp && cat f", true)).toBeDefined();
		// Given a working directory, the tool runs the `cd` itself, so `cat f` is a later step.
		expect(run("cd /tmp && cat f", true, { cwd: "/" })).toBeUndefined();
	});
});

describe.each(ARMS)("arm %s", (_arm, leadingOnly) => {
	test("a write or edit rule still blocks in any segment", () => {
		expect(run("ls && echo x > f", leadingOnly)).toBeDefined();
		expect(run("sed -i 's/a/b/' f", leadingOnly)).toBeDefined();
		expect(run("ls && sed -i 's/a/b/' f", leadingOnly)).toBeDefined();
	});
	test("a hub rule still blocks a later or backgrounded step", () => {
		expect(run("ls && nohup server", leadingOnly)).toBeDefined();
		expect(run("make && sleep 9 &", leadingOnly)).toBeDefined();
	});
	test("a piped stage passes: no dedicated tool reads stdin", () => {
		expect(run("ps | grep x", leadingOnly)).toBeUndefined();
	});
	test("a rule whose tool the session lacks never blocks", () => {
		expect(run("cat f", leadingOnly, {}, ["bash", "grep"])).toBeUndefined();
	});
});

describe("the refusal", () => {
	test("names the matched step, the tool for it and the original command", () => {
		const reason = run("ls d && cat f", false) ?? "";
		expect(reason).toContain("`cat f`");
		expect(reason).toContain("`read`");
		expect(reason).toContain("No step of this command ran. Run the command's other steps with bash");
		expect(reason).toEndWith("Original command: ls d && cat f");
	});
	test("for a single-step command, does not mention other steps", () => {
		const reason = run("sed -i 's/a/b/' f", true) ?? "";
		expect(reason).toContain("`sed -i 's/a/b/' f`");
		expect(reason).toContain("`edit`");
		expect(reason).not.toContain("other steps");
	});
	test("names the whole command when no single segment matches (a trailing &)", () => {
		expect(run("make && sleep 9 &", true)).toContain("`make && sleep 9 &`");
	});
});
