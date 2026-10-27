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
		expect(reason).toContain("Nothing ran. Re-send the rest as one bash call:\nls d");
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

describe("the refusal's runnable remainder", () => {
	const rest = (command: string, leadingOnly = false, extra: Record<string, unknown> = {}) => {
		const reason = run(command, leadingOnly, extra) ?? "";
		const offered = reason.split("\n\nNothing ran. Re-send the rest as one bash call:\n")[1];
		return offered?.split("\n\nOriginal command: ")[0];
	};

	test.each([
		["a sequenced step", "head -c 20 notes.md; echo; ls out*", "echo; ls out*"],
		["the last step", "make build; cat log.txt", "make build"],
		["a step on its own line", "make build\ncat log.txt\necho done", "make build\necho done"],
		["every blocked step at once", "ls src; cat a.txt; echo mid; grep -n x b.txt", "ls src; echo mid"],
		["the whole pipeline a blocked step feeds", "ls src; cat a.txt | wc -l; echo done", "ls src; echo done"],
		["a step guarded by an earlier one", "make build && cat log.txt", "make build"],
		["a backgrounded neighbour's `&`", "make build & cat log.txt; echo done", "make build & echo done"],
		["a comment the parser skips", "ls src # note\ncat a.txt\necho done", "ls src\necho done"],
	])("keeps %s", (_case, command, expected) => {
		expect(rest(command)).toBe(expected);
	});

	test.each([
		["a later command would become unconditional", "cat log.txt && rm log.txt"],
		["a later step reads the exit status", "cat log.txt; rc=$?; echo $rc"],
		["a later step reads a file the blocked step wrote", "echo hi > out.txt; wc -l out.txt"],
		["the command cannot be split", "cat $(ls); ls"],
		["the blocked step is the whole command", "cat log.txt"],
		["what is left only prepares the shell", "cd /srv && cat log.txt"],
		["what is left would be refused in turn", "cat a.txt; sleep 9 &"],
	])("offers nothing when %s", (_case, command) => {
		expect(run(command, false)).toBeDefined();
		expect(rest(command)).toBeUndefined();
	});

	test("keeps the leading cd the bash tool would take as the working directory", () => {
		expect(rest("cd /srv && cat log.txt; ls")).toBe("cd /srv; ls");
	});

	test("on: drops the leading step the arm blocks, after the cd the tool owns", () => {
		expect(rest("cd /srv && cat log.txt; ls", true)).toBe("cd /srv; ls");
	});

	test("offers only text the gate itself would let through", () => {
		for (const command of ["head -c 20 notes.md; echo; ls out*", "ls src; cat a.txt; echo mid; grep -n x b.txt", "make build & cat log.txt; echo done"]) {
			const offered = rest(command);
			expect(offered).toBeDefined();
			expect(run(offered ?? "", false)).toBeUndefined();
		}
	});

	test("says nothing about other steps when the parser cannot split the call", () => {
		const reason = run("cat $(ls) && ls", false) ?? "";
		expect(reason).not.toContain("other steps");
		expect(reason).not.toContain("Re-send the rest");
	});
});
