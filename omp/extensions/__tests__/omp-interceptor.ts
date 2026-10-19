// omp's own interceptor modules for the bash gate's tests: its rules (exec/settings), its
// checkBashInterception and its shell segment parser. Inside omp the extension imports them from
// omp's bundled copy; `bun test` resolves no @oh-my-pi package, so this asks Bun, outside the test
// runner, where it resolves @oh-my-pi/pi-coding-agent from this directory (its install cache), and
// imports those files by path. The rules module's settings registry imports @oh-my-pi/pi-utils,
// whose native addon does not load outside omp; it only logs there, so a stub stands in for it.
import { mock } from "bun:test";
import type { Interceptor, Rule } from "../experiments/bash-gate-core";

mock.module("@oh-my-pi/pi-utils", () => ({ logger: { debug() {}, warn() {}, error() {} }, parseFlag: (value: string) => ["1", "y", "yes", "true"].includes(value.toLowerCase()) }));

function locate(specifier: string): string {
	const found = Bun.spawnSync([process.execPath, "-e", `console.log(Bun.resolveSync(${JSON.stringify(specifier)}, process.cwd()))`], { cwd: import.meta.dir });
	if (found.exitCode !== 0) throw new Error(`omp-interceptor: Bun cannot resolve ${specifier}: ${found.stderr.toString()}`);
	return found.stdout.toString().trim();
}

// Imported by the path `locate` found, which is known only at run time.
const interceptor = await import(locate("@oh-my-pi/pi-coding-agent/tools/bash-interceptor"));
const tokenize = await import(locate("@oh-my-pi/pi-coding-agent/tools/shell-tokenize"));
const execSettings = await import(locate("@oh-my-pi/pi-coding-agent/exec/settings"));

export const ompModules = { interceptor, tokenize, execSettings };
export const OMP: Interceptor = { check: interceptor.checkBashInterception, segments: tokenize.extractFlatShellCommandSegments, leadingCd: tokenize.extractLeadingCdTarget };
export const DEFAULT_RULES: Rule[] = execSettings.DEFAULT_BASH_INTERCEPTOR_RULES;
