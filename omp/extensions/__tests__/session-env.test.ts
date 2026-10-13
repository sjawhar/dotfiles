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
