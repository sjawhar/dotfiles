// The bash gate: omp's bash interceptor run from an extension, so its two arms can differ in one
// thing. omp blocks a bash call when one of its interceptor rules (`bashInterceptor.patterns`)
// matches the whole command or any of its segments that does not read piped stdin. The `off` arm
// does exactly that. The `on` arm matches a rule whose tool is read, grep or glob against the
// command's leading segment only, so `ls d && cat f` runs while `cat f` and `cat f && ls d` are
// still blocked; every other rule (the write and edit redirects, the hub rules) still matches every
// segment. The leading segment is the first one omp's segment parser finds, or the whole command
// when the parser cannot split it (a heredoc, a command substitution, grouping).
//
// Matching is omp's own: `check` is checkBashInterception, called one rule at a time, which tests
// a command's whole text, each non-piped segment and each segment without its leading `NAME=value`
// words. As in omp's bash tool, a leading `cd <path> &&` that the tool turns into the call's
// working directory is checked both ways, with and without the `cd`, unless the call names a
// working directory; a call that names a service drops the rules whose tool is bash.
//
// The refusal names the step that matched, the tool to use for it and the original command, and —
// when the rest of the call can be re-sent unchanged — that exact text (bash-remainder.ts), so the
// agent does not re-split the command by hand. Nothing runs either way.

import { remainder } from "./bash-remainder";

export type Rule = { pattern: string; flags?: string; tool: string; message: string };
export type Segment = { text: string; pipedStdin: boolean };

/** omp's interceptor functions, bound by bash-gate.ts. */
export interface Interceptor {
	/** Whether `rules` block `command`, testing its whole text and every non-piped segment; a rule whose tool is not in `tools` never blocks. */
	check(command: string, tools: string[], rules: Rule[]): { block: boolean };
	/** The command's flat segments, or none when it cannot be split. */
	segments(command: string): Segment[];
	/** A leading `cd <path> && rest`, split, or null. */
	leadingCd(command: string): { path: string; rest: string } | null;
}

/** The tools that replace exploration; in the `on` arm their rules match the leading segment only. */
export const READ_ONLY_TOOLS: Record<string, true> = { read: true, grep: true, glob: true };

/** Whether `rule` blocks `segment`; in the `on` arm a read-only rule blocks the leading segment only. */
function blocks(omp: Interceptor, rule: Rule, segment: Segment, index: number, leading: number, tools: string[], leadingOnly: boolean): boolean {
	if (leadingOnly && READ_ONLY_TOOLS[rule.tool] && index !== leading) return false;
	return !segment.pipedStdin && omp.check(segment.text, tools, [rule]).block;
}

/** The segment of `command` that `rule` blocks; the whole command when no single segment does (a trailing `&`). */
function blockedStep(omp: Interceptor, command: string, tools: string[], rule: Rule): string {
	for (const segment of omp.segments(command)) {
		if (!segment.pipedStdin && omp.check(segment.text, tools, [rule]).block) return segment.text;
	}
	return command.trim();
}

/**
 * The first active rule that refuses `raw`, with the text it matched. `cwd` says whether the bash
 * tool already owns a leading `cd <path> &&`; without it the gate checks the command both ways.
 */
function match(omp: Interceptor, raw: string, cwd: boolean, active: readonly Rule[], tools: string[], leadingOnly: boolean): { rule: Rule; scope: string; leading: number } | undefined {
	let command = raw;
	if (!cwd) {
		const cd = omp.leadingCd(raw);
		if (cd) command = cd.rest;
	}
	for (const candidate of raw === command ? [raw] : [raw, command]) {
		for (const rule of active) {
			const scope = leadingOnly && READ_ONLY_TOOLS[rule.tool] ? (omp.segments(candidate)[0]?.text ?? candidate) : candidate;
			if (omp.check(scope, tools, [rule]).block) return { rule, scope, leading: candidate === raw ? 0 : 1 };
		}
	}
	return undefined;
}

/** Why the bash call with `input` is refused, or undefined when it may run. `leadingOnly` is the `on` arm. */
export function intercept(omp: Interceptor, input: Record<string, unknown>, rules: readonly Rule[], tools: string[], leadingOnly: boolean): string | undefined {
	const raw = input.command;
	if (typeof raw !== "string") return undefined;
	const service = typeof input.name === "string" && input.name.trim() !== "";
	const active = rules.filter(rule => !service || rule.tool !== "bash");
	const cwd = Boolean(input.cwd);
	const found = match(omp, raw, cwd, active, tools, leadingOnly);
	if (!found) return undefined;
	const step = blockedStep(omp, found.scope, tools, found.rule);
	// The whole call is refused, so the rest has to be re-sent. Offer that text when it is safe,
	// built from the command as written: the `cd` the bash tool would turn into the call's working
	// directory is the leading segment of `raw`, so the arm's leading segment is the one after it
	// when the match came from the `cd`-stripped form.
	const segments = omp.segments(raw);
	const blocked = segments.map((segment, index) => (active.some(rule => blocks(omp, rule, segment, index, found.leading, tools, leadingOnly)) ? index : -1)).filter(index => index >= 0);
	const rest = remainder(omp, raw, segments, blocked, text => match(omp, text, cwd, active, tools, leadingOnly) !== undefined);
	const next =
		rest === undefined
			? segments.length > 1 && step !== raw.trim()
				? "\n\nNo step of this command ran. Run the command's other steps with bash, without that one."
				: ""
			: `\n\nNothing ran. Re-send the rest as one bash call:\n${rest}`;
	return `Blocked: \`${step}\` has a dedicated tool, \`${found.rule.tool}\`. ${found.rule.message}${next}\n\nOriginal command: ${raw}`;
}
