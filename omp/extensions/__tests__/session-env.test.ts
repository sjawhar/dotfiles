import { afterEach, describe, expect, test } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { userConfigChain } from "../session-env";

const dirs: string[] = [];
/** An environment whose PATH finds a `jj` running `body` first. */
function withJj(body: string): NodeJS.ProcessEnv {
	const dir = mkdtempSync(path.join(os.tmpdir(), "session-env-jj-"));
	dirs.push(dir);
	writeFileSync(path.join(dir, "jj"), `#!/bin/sh\n${body}\n`);
	chmodSync(path.join(dir, "jj"), 0o755);
	return { ...process.env, PATH: `${dir}:${process.env.PATH ?? ""}` };
}
afterEach(() => {
	for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("userConfigChain", () => {
	test("chains the user config files jj prints, in order", async () => {
		expect(await userConfigChain(5_000, withJj("printf '/u/a.toml\\n/u/b.toml\\n'"))).toBe("/u/a.toml:/u/b.toml");
	});

	test("gives up on a jj that never answers, so session_start never waits on it", async () => {
		const started = Date.now();
		expect(await userConfigChain(200, withJj("exec sleep 30"))).toBeUndefined();
		expect(Date.now() - started).toBeLessThan(4_000);
	}, 5_000);
});

describe("the Python eval kernel's environment", () => {
	// omp starts the Python eval kernel with its built-in allowlist only (fixed names plus the
	// LC_, XDG_ and PI_ prefixes, packages/coding-agent/src/eval/py/runtime.ts) and what
	// eval.envPassthrough in config.yml admits. A variable this extension exports that neither
	// admits never reaches a command run from an eval cell: agent-secrets lost the session that way.
	const OMP_BUILTIN_PREFIXES = ["LC_", "XDG_", "PI_"];

	test("admits every variable session-env exports", async () => {
		const source = await Bun.file(path.join(import.meta.dir, "..", "session-env.ts")).text();
		const exported = [...new Set(Array.from(source.matchAll(/process\.env\.([A-Z][A-Z0-9_]*)\s*=(?!=)/g), m => m[1]))];
		expect(exported).toEqual(expect.arrayContaining(["OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "JJ_CONFIG"]));
		const config = Bun.YAML.parse(await Bun.file(path.join(import.meta.dir, "..", "..", "config.yml")).text()) as {
			eval?: { envPassthrough?: { names?: string[]; prefixes?: string[] } };
		};
		const { names = [], prefixes = [] } = config.eval?.envPassthrough ?? {};
		const allowedPrefixes = [...OMP_BUILTIN_PREFIXES, ...prefixes];
		const dropped = exported.filter(key => !names.includes(key) && !allowedPrefixes.some(prefix => key.startsWith(prefix)));
		expect(dropped).toEqual([]);
	});
});
