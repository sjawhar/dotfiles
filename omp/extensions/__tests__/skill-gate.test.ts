import { describe, expect, test } from "bun:test";
import feature, { loadedSkills, skillsNeeded } from "../experiments/skill-gate";

type Entry = { type: string; id?: string; [key: string]: unknown };
type Result = { block?: boolean; reason?: string } | undefined;

let n = 0;
const id = () => `e${++n}`;
const call = (name: string, args: Record<string, unknown>, callId = `c${++n}`): Entry => ({
	type: "message",
	id: id(),
	message: { role: "assistant", provider: "anthropic", content: [{ type: "toolCall", id: callId, name, arguments: args }] },
});
const result = (callId: string, toolName = "read", isError = false): Entry => ({
	type: "message",
	id: id(),
	message: { role: "toolResult", toolCallId: callId, toolName, isError, content: [{ type: "text", text: "…" }] },
});
const skillRead = (skill: string, ok = true): Entry[] => {
	const c = `r${++n}`;
	return [call("read", { path: `skill://${skill}` }, c), result(c, "read", !ok)];
};
const skillPrompt = (name: string): Entry => ({ type: "custom_message", id: id(), customType: "skill-prompt", details: { name } });
const compaction = (firstKeptEntryId: string): Entry => ({ type: "compaction", id: id(), summary: "…", firstKeptEntryId });
const reset = (): Entry => ({ type: "reset_boundary", id: id() });
const userMsg = (): Entry => ({ type: "message", id: id(), message: { role: "user", content: "next" } });

/** The feature bound to a fake `pi`; `fire` runs its tool_call hook against `branch`. */
function bind() {
	let handler: ((event: unknown, ctx: unknown) => Result) | undefined;
	feature({ on: (_event: string, h: typeof handler) => (handler = h) } as unknown as Parameters<typeof feature>[0]);
	const branch: Entry[] = [userMsg()];
	const fire = (toolName: string, input: Record<string, unknown>) => handler?.({ toolName, toolCallId: `c${++n}`, input }, { sessionManager: { getBranch: () => branch } });
	return { branch, fire };
}

const PR = ["opening-a-pr"];
const PUSH = ["using-jj"];

describe("which calls need a skill", () => {
	test.each([
		["bash", { command: "gh pr create --head x" }, PR],
		["bash", { command: "gh pr new --fill" }, PR],
		["bash", { command: "legion gh -- pr create --fill" }, PR],
		["bash", { command: "gh -R owner/repo pr create --fill" }, PR],
		["bash", { command: "gh --repo=owner/repo pr new" }, PR],
		["bash", { command: "jj git push --bookmark main" }, PUSH],
		["bash", { command: "cd ~/x && jj --ignore-working-copy git push -b main" }, PUSH],
		["bash", { command: "jj -R ~/src/x git push --named a=@" }, PUSH],
		["bash", { command: "jj git push --bookmark x && gh pr create --fill" }, ["opening-a-pr", "using-jj"]],
		// Neither command: other jj and gh calls, a plain git push, read-only gh.
		["bash", { command: "jj git fetch && jj log" }, []],
		["bash", { command: "git push origin main" }, []],
		["bash", { command: "gh pr view 12 && gh api repos/x/pulls --jq '.[] | .title'" }, []],
		["bash", { command: "echo hi" }, []],
		// Shell commands run from eval code: a shell API together with the command in command position.
		["eval", { language: "py", code: "subprocess.run('gh pr create --fill', shell=True, check=True)" }, PR],
		["eval", { language: "py", code: "subprocess.run(['gh', '-R', repo, 'pr', 'create', '--fill'], check=True)" }, PR],
		["eval", { language: "py", code: "subprocess.run(['secrets', 'GH_TOKEN', '--', 'gh', 'pr', 'create', '--fill'])" }, PR],
		["eval", { language: "py", code: "subprocess.run(['bash', '-c', '''\nnice -n 19 gh pr create --fill\n'''])" }, PR],
		["eval", { language: "py", code: "import subprocess\nsubprocess.run(['jj', 'git', 'push', '-b', 'main'], check=True)" }, PUSH],
		["eval", { language: "py", code: "os.system('cd x && timeout 90 jj git push')" }, PUSH],
		["eval", { language: "py", code: "!jj git push --bookmark main" }, PUSH],
		["eval", { language: "py", code: "%%bash\ncd ~/x\njj git push -b main" }, PUSH],
		["eval", { language: "js", code: "await $`jj git push -b main`" }, PUSH],
		["eval", { language: "js", code: "const { execSync } = require('node:child_process');\nexecSync(`\n  cd x\n  jj git push\n`)" }, PUSH],
		["eval", { language: "py", code: "subprocess.run(['/usr/local/bin/jj', 'git', 'push'])" }, PUSH],
		// Eval code that names a command without shelling out, or names it outside command position.
		["eval", { language: "py", code: "print('run jj git push next')" }, []],
		["eval", { language: "py", code: "import subprocess\n# then gh pr create\nprint('done')" }, []],
		["eval", { language: "py", code: "import subprocess\nmsg = 'Use `jj git push` afterwards.'\nsubprocess.run(['envoy', 'send', msg])" }, []],
		["eval", { language: "py", code: "subprocess.run(['jj', 'git', 'fetch'])" }, []],
		// Another tool, even one naming the command.
		["read", { path: "skill://using-jj" }, []],
		["write", { path: "deploy.sh", content: "jj git push\n" }, []],
	] as const)("%s %j → %j", (tool, input, expected) => expect(skillsNeeded(tool, input as Record<string, unknown>)).toEqual([...expected]));
	test("a cell carrying 32k spaces, a 32k word or an unclosed backtick is decided in well under a second (the gate runs in the session)", () => {
		for (const filler of [" ".repeat(32_000), "a".repeat(32_000), `\`${"a\\`b\n".repeat(10_000)}`]) {
			const started = performance.now();
			expect(skillsNeeded("eval", { language: "py", code: `import subprocess\nsubprocess.run('${filler}')` })).toEqual([]);
			expect(performance.now() - started).toBeLessThan(500);
		}
	});
});

describe("loadedSkills", () => {
	test("a completed read counts; a pending or errored read does not", () => {
		expect(loadedSkills([...skillRead("using-jj")])).toEqual(new Set(["using-jj"]));
		expect(loadedSkills([call("read", { path: "skill://using-jj" })])).toEqual(new Set());
		expect(loadedSkills([...skillRead("using-jj", false)])).toEqual(new Set());
	});
	test("SKILL.md suffix counts, a reference file does not, a /skill: prompt counts", () => {
		const c = `c${++n}`;
		expect(loadedSkills([call("read", { path: "skill://using-jj/SKILL.md" }, c), result(c)])).toEqual(new Set(["using-jj"]));
		const d = `c${++n}`;
		expect(loadedSkills([call("read", { path: "skill://using-jj/references/x.md" }, d), result(d)])).toEqual(new Set());
		expect(loadedSkills([skillPrompt("opening-a-pr")])).toEqual(new Set(["opening-a-pr"]));
	});
	test("only entries from the latest compaction's first kept entry count; a later /clear is the bound", () => {
		const before = skillRead("using-jj");
		const kept = skillRead("opening-a-pr");
		const branch = [...before, ...kept, compaction(kept[0].id as string)];
		expect(loadedSkills(branch)).toEqual(new Set(["opening-a-pr"]));
		const cleared = [...branch, reset()];
		expect(loadedSkills(cleared)).toEqual(new Set());
		expect(loadedSkills([...cleared, ...skillRead("using-jj")])).toEqual(new Set(["using-jj"]));
	});
});

describe("the refusal", () => {
	test("refuses gh pr create naming skill://opening-a-pr, and lets it run once the skill is read", () => {
		const g = bind();
		const r = g.fire("bash", { command: "gh pr create --fill" });
		expect(r?.block).toBe(true);
		expect(r?.reason).toContain("skill://opening-a-pr");
		expect(r?.reason).toContain("same batch");
		g.branch.push(...skillRead("opening-a-pr"));
		expect(g.fire("bash", { command: "gh pr create --fill" })).toBeUndefined();
	});
	test("refuses jj git push naming skill://using-jj; the other skill does not satisfy it", () => {
		const g = bind();
		g.branch.push(...skillRead("opening-a-pr"));
		const r = g.fire("eval", { language: "py", code: "subprocess.run(['jj', 'git', 'push'])" });
		expect(r?.block).toBe(true);
		expect(r?.reason).toContain("skill://using-jj");
		expect(r?.reason).not.toContain("opening-a-pr");
	});
	test("keeps refusing however often it is retried, and starts refusing again after a /clear", () => {
		const g = bind();
		for (let i = 0; i < 4; i++) expect(g.fire("bash", { command: "jj git push" })?.block).toBe(true);
		g.branch.push(...skillRead("using-jj"));
		expect(g.fire("bash", { command: "jj git push" })).toBeUndefined();
		g.branch.push(reset());
		expect(g.fire("bash", { command: "jj git push" })?.block).toBe(true);
	});
	test("passes every other call untouched", () => {
		expect(bind().fire("bash", { command: "jj log && gh pr view" })).toBeUndefined();
	});
});
