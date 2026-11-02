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
// The refusal names the step that matched, the tool to use for it and the original command, and,
// for a call of several steps, says that none of them ran. Nothing runs either way. It does not
// hand back the rest of the call: that text can drop what the agent wanted around the blocked step
// (a `| grep`, a guard), and the agent can write the call again (dispatch://INFRA-161).

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
function match(omp: Interceptor, raw: string, cwd: boolean, active: readonly Rule[], tools: string[], leadingOnly: boolean): { rule: Rule; scope: string } | undefined {
	let command = raw;
	if (!cwd) {
		const cd = omp.leadingCd(raw);
		if (cd) command = cd.rest;
	}
	for (const candidate of raw === command ? [raw] : [raw, command]) {
		for (const rule of active) {
			const scope = leadingOnly && READ_ONLY_TOOLS[rule.tool] ? (omp.segments(candidate)[0]?.text ?? candidate) : candidate;
			if (omp.check(scope, tools, [rule]).block) return { rule, scope };
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
	// The whole call is refused; agents read a refusal of a later step as if the earlier steps had run.
	const next = omp.segments(raw).length > 1 && step !== raw.trim() ? "\n\nNo step of this command ran." : "";
	return `Blocked: \`${step}\` has a dedicated tool, \`${found.rule.tool}\`. ${found.rule.message}${next}\n\nOriginal command: ${raw}`;
}
