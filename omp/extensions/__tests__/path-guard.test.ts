import { afterEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { createPathGuard, type Env } from "../path-guard-core";

type Result = { block?: boolean; reason?: string } | undefined;
type Handler = (event: unknown, ctx: unknown) => Result | Promise<Result>;

const W = "/work/out";
const R = "/work/src";

/** omp's edit-target parser stands in as the shape of each input: write and edit name `path`, ast_edit `paths`. */
function editTargets(toolName: string, input: Record<string, unknown>): string[] {
	if (toolName === "ast_edit") return input.paths as string[];
	return typeof input.path === "string" ? [input.path] : [];
}

/** The guard bound to a fake `pi` under `env`; `fire` runs one tool call and returns the refusal text, if any. */
function bind(env: Env) {
	let handler: Handler | undefined;
	const debug: unknown[] = [];
	const pi = {
		on: (event: string, h: Handler) => {
			if (event === "tool_call") handler = h;
		},
		logger: { debug: (...args: unknown[]) => void debug.push(args) },
	};
	createPathGuard({ HOME: "/home/u", ...env }, editTargets, "/launch")(pi as never);
	const fire = async (toolName: string, input: Record<string, unknown>, cwd = W): Promise<string | undefined> => {
		if (!handler) throw new Error("no tool_call handler registered");
		const result = await handler({ type: "tool_call", toolName, toolCallId: "t1", input }, { cwd });
		if (result === undefined) return undefined;
		expect(result.block).toBe(true);
		return result.reason;
	};
	const bash = (command: string, cwd = W, extra: Record<string, unknown> = {}) => fire("bash", { command, ...extra }, cwd);
	return { registered: () => handler !== undefined, fire, bash, debug };
}

describe("off unless a list is set", () => {
	test("neither variable set: no handler is registered", () => {
		expect(bind({}).registered()).toBe(false);
	});

	test("empty lists count as unset", () => {
		expect(bind({ SCHED_ALLOW_WRITE: "", SCHED_ALLOW_READ: " : " }).registered()).toBe(false);
	});

	test("either variable alone registers the handler", () => {
		expect(bind({ SCHED_ALLOW_WRITE: W }).registered()).toBe(true);
		expect(bind({ SCHED_ALLOW_READ: R }).registered()).toBe(true);
	});
});

describe("write list", () => {
	const g = () => bind({ SCHED_ALLOW_WRITE: `${W}:~/notes:rel` });

	test("a write inside an allowed directory runs", async () => {
		expect(await g().fire("write", { path: `${W}/a.md` })).toBeUndefined();
		expect(await g().fire("write", { path: "a.md" })).toBeUndefined();
		expect(await g().fire("write", { path: "/home/u/notes/x" })).toBeUndefined();
		expect(await g().fire("write", { path: "/launch/rel/x" })).toBeUndefined();
	});

	test("a write outside is refused, naming the target and every allowed directory", async () => {
		const reason = await g().fire("write", { path: "/etc/passwd" });
		expect(reason).toContain("/etc/passwd");
		expect(reason).toContain("SCHED_ALLOW_WRITE");
		expect(reason).toContain(W);
		expect(reason).toContain("/home/u/notes");
		expect(reason).toContain("/launch/rel");
	});

	test("a relative escape and a sibling with the same prefix are outside", async () => {
		expect(await g().fire("write", { path: "../x" })).toContain("/work/x");
		expect(await g().fire("write", { path: "/work/outside/x" })).toContain("/work/outside/x");
	});

	test("internal URLs pass", async () => {
		expect(await g().fire("write", { path: "xd://envoy_send", content: "{}" })).toBeUndefined();
		expect(await g().fire("write", { path: "local://notes.md" })).toBeUndefined();
	});

	test("edit and ast_edit targets are checked; ast_edit globs by their fixed prefix", async () => {
		expect(await g().fire("edit", { path: `${W}/a.ts` })).toBeUndefined();
		expect(await g().fire("edit", { path: "/work/src/a.ts" })).toContain("/work/src/a.ts");
		expect(await g().fire("ast_edit", { paths: ["**/*.ts"] })).toBeUndefined();
		expect(await g().fire("ast_edit", { paths: ["/work/**/*.ts"] })).toContain("/work");
	});

	test("reads are not limited by a write list alone", async () => {
		expect(await g().fire("read", { path: "/etc/passwd" })).toBeUndefined();
		expect(await g().bash("cat /etc/passwd > copy.txt")).toBeUndefined();
	});

	test("eval, whose writes the guard cannot see, is refused under a write list and not under a read list alone", async () => {
		const reason = await g().fire("eval", { language: "py", code: "import subprocess; subprocess.run('date > /etc/x', shell=True)" });
		expect(reason).toContain("eval");
		expect(reason).toContain("SCHED_ALLOW_WRITE");
		expect(reason).toContain(W);
		expect(await bind({ SCHED_ALLOW_READ: R }).fire("eval", { language: "py", code: "1 + 1" })).toBeUndefined();
	});
});

describe("bash writes", () => {
	const bash = (command: string, cwd?: string, extra?: Record<string, unknown>) => bind({ SCHED_ALLOW_WRITE: W }).bash(command, cwd, extra);

	test("redirections inside run", async () => {
		expect(await bash("date > out.txt")).toBeUndefined();
		expect(await bash(`date >> ${W}/log 2>&1`)).toBeUndefined();
		expect(await bash("make 2>/dev/null >&2")).toBeUndefined();
	});

	test("each redirection form outside is refused", async () => {
		for (const command of ["date > /etc/x", "date >/etc/x", "date >> /etc/x", "date >| /etc/x", "make 2> /etc/x", "make &> /etc/x", "make &>>/etc/x", "make >& /etc/x"]) {
			expect(await bash(command)).toContain("/etc/x");
		}
	});

	test("redirect text inside quotes or a comment is not a redirect", async () => {
		expect(await bash('echo "a > /etc/x"')).toBeUndefined();
		expect(await bash("echo 'a' # > /etc/x")).toBeUndefined();
		expect(await bash('date > "/etc/my file"')).toContain("/etc/my file");
	});

	test("tee, cp, mv, rm, sed -i, touch and mkdir targets", async () => {
		expect(await bash("ls | tee -a /etc/x")).toContain("/etc/x");
		expect(await bash("ls | tee inside.txt")).toBeUndefined();
		expect(await bash("cp a /etc/")).toContain("/etc");
		expect(await bash("cp /etc/passwd ./copy")).toBeUndefined();
		expect(await bash("cp -t /etc a b")).toContain("/etc");
		expect(await bash("mv /etc/a ./b")).toContain("/etc/a");
		expect(await bash("rm -rf /etc/x")).toContain("/etc/x");
		expect(await bash("rm -f *.o")).toBeUndefined();
		expect(await bash("sed -i 's/a/b/' /etc/x")).toContain("/etc/x");
		expect(await bash("sed 's/a/b/' /etc/x")).toBeUndefined();
		expect(await bash("sed -i -e s/a/b/ inside.txt")).toBeUndefined();
		expect(await bash("touch /etc/x")).toContain("/etc/x");
		expect(await bash("mkdir -p -m 755 /etc/d")).toContain("/etc/d");
	});

	test("a later command, a prefix word or a full path still counts", async () => {
		expect(await bash("ls && rm /etc/x")).toContain("/etc/x");
		expect(await bash("FOO=1 rm /etc/x")).toContain("/etc/x");
		expect(await bash("sudo rm /etc/x")).toContain("/etc/x");
		expect(await bash("/bin/rm /etc/x")).toContain("/etc/x");
		expect(await bash("x=$(touch /etc/x)")).toContain("/etc/x");
		expect(await bash("if true; then rm /etc/x; fi")).toContain("/etc/x");
	});

	test("`<` and `>` in [[ ]] and arithmetic compare, not redirect", async () => {
		expect(await bash("if [[ a > b ]]; then ls; fi", "/etc")).toBeUndefined();
		expect(await bash("if (( n > 0 )); then ls; fi", "/etc")).toBeUndefined();
		expect(await bash("echo $((n > 0)) > out.txt")).toBeUndefined();
		expect(await bash("echo $((n > 0)) > /etc/x")).toContain("/etc/x");
	});

	test("cd moves relative targets; a subshell's cd ends with it; the call's cwd counts", async () => {
		expect(await bash("cd /etc && touch x")).toContain("/etc/x");
		expect(await bash("cd sub && date > x")).toBeUndefined();
		expect(await bash("(cd /etc && ls) && date > x")).toBeUndefined();
		expect(await bash("date > x", W, { cwd: "/etc" })).toContain("/etc/x");
		expect(await bash("date > x", "/etc")).toContain("/etc/x");
	});

	test("a heredoc body is not run as commands", async () => {
		expect(await bash("cat > inside.txt <<'EOF'\nrm -rf /etc\ndate > /etc/x\nEOF\nls")).toBeUndefined();
		expect(await bash("cat > inside.txt <<EOF\nbody\nEOF\nrm /etc/x")).toContain("/etc/x");
	});

	test("known variables expand; an unknown one is let through", async () => {
		expect(await bash('date > "$HOME/x"')).toContain("/home/u/x");
		expect(await bash("date > ${HOME}/x")).toContain("/home/u/x");
		expect(await bash('date > "$NOT_SET/x"')).toBeUndefined();
		expect(await bash("date > ~/x")).toContain("/home/u/x");
	});

	test("process substitution is not a file target", async () => {
		expect(await bash("ls | tee >(gzip > inside.gz)")).toBeUndefined();
		expect(await bash("ls | tee >(gzip > /etc/x.gz)")).toContain("/etc/x.gz");
	});

	test("every outside target is listed once", async () => {
		const reason = await bash("touch /etc/a /etc/b; rm /etc/a");
		expect(reason).toContain("/etc/a, /etc/b");
		expect(reason?.match(/\/etc\/a/g)?.length).toBe(1);
	});
});

describe("read list", () => {
	const g = () => bind({ SCHED_ALLOW_READ: R, SCHED_ALLOW_WRITE: W });

	test("read inside the read list or the write list runs", async () => {
		expect(await g().fire("read", { path: `${R}/a.ts` })).toBeUndefined();
		expect(await g().fire("read", { path: `${R}/a.ts:10-20` })).toBeUndefined();
		expect(await g().fire("read", { path: "a.md" })).toBeUndefined();
	});

	test("read outside is refused, naming both variables and the readable directories", async () => {
		const reason = await g().fire("read", { path: "/etc/passwd:1-5" });
		expect(reason).toContain("/etc/passwd");
		expect(reason).toContain("SCHED_ALLOW_READ");
		expect(reason).toContain(R);
		expect(reason).toContain(W);
	});

	test("URLs pass", async () => {
		expect(await g().fire("read", { path: "skill://dispatch" })).toBeUndefined();
		expect(await g().fire("read", { path: "https://example.com/" })).toBeUndefined();
	});

	test("grep, glob and ast_grep paths, each part of a list, default the cwd", async () => {
		expect(await g().fire("grep", { pattern: "x" })).toBeUndefined();
		expect(await g().fire("grep", { pattern: "x" }, "/etc")).toContain("/etc");
		expect(await g().fire("grep", { pattern: "x", path: `${R}; /etc` })).toContain("/etc");
		expect(await g().fire("glob", { path: `${R}/**/*.ts` })).toBeUndefined();
		expect(await g().fire("glob", { path: "/work/**/*.ts" })).toContain("/work");
		expect(await g().fire("ast_grep", { pat: "x", path: "/etc/x.c" })).toContain("/etc/x.c");
		expect(await g().fire("grep", { pattern: "x", path: `${R}; !${R}/gen` })).toBeUndefined();
	});

	test("bash input redirects and cat/head/tail operands are reads", async () => {
		expect(await g().bash("wc -l < /etc/passwd")).toContain("/etc/passwd");
		expect(await g().bash("cat /etc/passwd")).toContain("/etc/passwd");
		expect(await g().bash("head -n 5 /etc/passwd")).toContain("/etc/passwd");
		expect(await g().bash(`head -n 5 ${R}/a.ts`)).toBeUndefined();
		expect(await g().bash("cp /etc/passwd ./copy")).toContain("/etc/passwd");
	});

	test("a read list alone leaves writes alone", async () => {
		const only = bind({ SCHED_ALLOW_READ: R });
		expect(await only.fire("write", { path: "/etc/x" })).toBeUndefined();
		expect(await only.fire("read", { path: "/etc/x" })).toContain("/etc/x");
	});
});

describe("symlinks", () => {
	const dirs: string[] = [];
	afterEach(() => {
		for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
	});

	test("a path through a symlink counts as the directory it reaches, both ways", async () => {
		const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), "path-guard-")));
		dirs.push(root);
		mkdirSync(path.join(root, "real"));
		symlinkSync(path.join(root, "real"), path.join(root, "link"));
		const viaLink = bind({ SCHED_ALLOW_WRITE: path.join(root, "link") });
		expect(await viaLink.fire("write", { path: path.join(root, "real", "new.txt") }, root)).toBeUndefined();
		const viaReal = bind({ SCHED_ALLOW_WRITE: path.join(root, "real") });
		expect(await viaReal.fire("write", { path: path.join(root, "link", "deeper", "new.txt") }, root)).toBeUndefined();
		expect(await viaReal.fire("write", { path: path.join(root, "x.txt") }, root)).toContain(path.join(root, "x.txt"));
	});
});

describe("fails open", () => {
	test("an exception in the check lets the call run and logs one debug line", async () => {
		const throwing = (): string[] => {
			throw new Error("boom");
		};
		let handler: Handler | undefined;
		const debug: unknown[] = [];
		const pi = { on: (_: string, h: Handler) => void (handler = h), logger: { debug: (...args: unknown[]) => void debug.push(args) } };
		createPathGuard({ SCHED_ALLOW_WRITE: W }, throwing, "/launch")(pi as never);
		expect(await handler?.({ type: "tool_call", toolName: "edit", toolCallId: "t", input: {} }, { cwd: W })).toBeUndefined();
		expect(debug.length).toBe(1);
	});
});
