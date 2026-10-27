// The runnable remainder of a refused bash call. The gate refuses the whole call: nothing runs, so
// the agent has to re-send the steps no rule named, and today it re-types them by hand. This builds
// that text from the gate's own segments, and returns nothing whenever the text would mean
// something other than what the agent wrote. The remainder is only offered; the gate still runs
// nothing.
//
// omp's segment parser gives each segment's text and its piped-stdin flag, not its offsets, so the
// segments are located in the command by scanning forward from the end of the previous one, and the
// operator between two segments is read from the gap. A comment can repeat a segment's text and
// move a match, so the rejoined text is parsed again and has to come back as exactly the kept
// segments before it is offered.

import type { Interceptor, Segment } from "./bash-gate-core";

/** Commands whose effect the steps after them inherit: dropping one changes what those steps see. */
const STATEFUL: Record<string, true> = { ".": true, alias: true, cd: true, declare: true, eval: true, export: true, popd: true, pushd: true, readonly: true, set: true, shopt: true, source: true, trap: true, typeset: true, ulimit: true, umask: true, unalias: true, unset: true };

/** Commands that only prepare the shell. A remainder of nothing but these costs a call and prints nothing. */
const PREPARATION_ONLY: Record<string, true> = { ".": true, ":": true, alias: true, cd: true, export: true, popd: true, pushd: true, set: true, shopt: true, source: true, true: true, ulimit: true, umask: true, unset: true };

/** `$?`, `${?}` and `$PIPESTATUS` read the exit status of whatever ran before. */
const EXIT_STATUS = /\$\?|\$\{\?\}|\$\{?PIPESTATUS/;

const LEADING_ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/;
const REDIRECT_TARGET = /(?:^|[\s;|&])(?:\d*>{1,2}\|?|&>{1,2})\s*("[^"]*"|'[^']*'|[^\s;|&<>]+)/g;
const TEE_TARGETS = /(?:^|[\s|])tee\s+(?:-\S+\s+)*((?:"[^"]*"|'[^']*'|[^\s;|&<>]+)(?:\s+(?:"[^"]*"|'[^']*'|[^\s;|&<>]+))*)/g;

/** The operator that ends a segment; "" ends the last one. */
type Operator = "" | "&&" | "||" | ";" | "|" | "|&" | "&" | "\n";

/** The first word of a segment, without its leading `NAME=value` words and without its directory. */
function commandWord(text: string): string {
	let rest = text;
	while (true) {
		const assignment = /^([A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S)*)\s+/.exec(rest);
		if (!assignment) break;
		rest = rest.slice(assignment[0].length);
	}
	const word = /^([^\s;|&<>]+)/.exec(rest)?.[1] ?? "";
	return word.split("/").pop() ?? word;
}

/** Paths the text writes through a redirect or `tee`, ignoring the shell's own device sinks. */
function writtenPaths(text: string): string[] {
	const paths: string[] = [];
	const unquote = (word: string) => ((word[0] === '"' || word[0] === "'") && word.endsWith(word[0]) ? word.slice(1, -1) : word);
	for (const match of text.matchAll(REDIRECT_TARGET)) paths.push(unquote(match[1] ?? ""));
	for (const match of text.matchAll(TEE_TARGETS)) for (const word of (match[1] ?? "").split(/\s+/)) paths.push(unquote(word));
	return paths.filter(path => path.length > 0 && !path.startsWith("/dev/"));
}

/** Each segment's end offset in `command` and the operator that follows it, or null when a segment cannot be placed. */
function placeSegments(command: string, segments: Segment[]): Operator[] | null {
	const ends: number[] = [];
	let cursor = 0;
	for (const segment of segments) {
		const start = command.indexOf(segment.text, cursor);
		if (start < 0) return null;
		cursor = start + segment.text.length;
		ends.push(cursor);
	}
	const operators: Operator[] = [];
	for (const [index, end] of ends.entries()) {
		const next = segments[index + 1];
		if (!next) {
			// A trailing `&` backgrounds the last segment; anything else ends the command.
			operators.push(/^\s*&\s*$/.test(command.slice(end)) ? "&" : "");
			continue;
		}
		if (next.pipedStdin) {
			operators.push(command.slice(end, command.indexOf(next.text, end)).includes("|&") ? "|&" : "|");
			continue;
		}
		// A `#` comment runs to the end of its line, so only the gap before it holds the operator.
		const gap = command.slice(end, command.indexOf(next.text, end)).split("#")[0];
		const found = (["&&", "||", ";", "&", "\n"] as const).map(operator => ({ operator, at: gap.indexOf(operator) })).filter(hit => hit.at >= 0);
		found.sort((a, b) => a.at - b.at);
		operators.push(found[0]?.operator ?? "\n");
	}
	return operators;
}

/**
 * The text the agent can re-send after the gate refused the whole call: `command` without the
 * blocked steps and the pipeline each sits in, or undefined when that text would not mean what the
 * agent wrote. `blocked` are indices into `segments`; `refused` says whether a candidate remainder
 * would be refused in turn.
 */
export function remainder(omp: Interceptor, command: string, segments: Segment[], blocked: number[], refused: (candidate: string) => boolean): string | undefined {
	if (blocked.length === 0 || segments.length === 0) return undefined;
	const operators = placeSegments(command, segments);
	if (!operators) return undefined;

	// A pipeline's stages share one stdin/stdout chain, so a blocked stage takes its pipeline with it.
	const drop = new Set<number>();
	for (const index of blocked) {
		let first = index;
		while (first > 0 && segments[first].pipedStdin) first--;
		let last = index;
		while (last + 1 < segments.length && segments[last + 1].pipedStdin) last++;
		for (let step = first; step <= last; step++) drop.add(step);
	}
	const kept = segments.filter((_, index) => !drop.has(index));
	if (kept.every(segment => PREPARATION_ONLY[commandWord(segment.text)] || LEADING_ASSIGNMENT.test(commandWord(segment.text)))) return undefined;

	// Dropping a step must not change what the steps after it see.
	for (const index of drop) {
		const segment = segments[index];
		if (!drop.has(index + 1) && (operators[index] === "&&" || operators[index] === "||")) return undefined;
		const later = segments.filter((_, other) => other > index && !drop.has(other));
		if (later.length === 0) continue;
		if (later.some(step => EXIT_STATUS.test(step.text))) return undefined;
		if (LEADING_ASSIGNMENT.test(segment.text) || STATEFUL[commandWord(segment.text)]) return undefined;
		const written = writtenPaths(segment.text);
		if (later.some(step => written.some(path => step.text.includes(path)))) return undefined;
	}

	// Rejoin the kept steps. Where a dropped step sat between two kept ones, its operator goes too:
	// only `;` (or the `&` that backgrounds the step before it) still says what the kept steps do.
	const lastKept = segments.reduce((last, _, index) => (drop.has(index) ? last : index), -1);
	const pieces: string[] = [];
	for (const [index, segment] of segments.entries()) {
		if (drop.has(index)) continue;
		pieces.push(segment.text);
		if (index === lastKept) {
			if (operators[index] === "&") pieces.push(" &");
			continue;
		}
		const operator = drop.has(index + 1) && operators[index] !== "&" && operators[index] !== "\n" ? ";" : operators[index];
		pieces.push(operator === "\n" ? "\n" : operator === ";" ? "; " : ` ${operator} `);
	}
	const rejoined = pieces.join("").trim();
	if (rejoined.length === 0 || rejoined === command.trim()) return undefined;

	// The rejoined text has to parse back into exactly the kept steps, or the scan above read the
	// command differently than the shell will.
	const reparsed = omp.segments(rejoined);
	const texts = reparsed.length > 0 ? reparsed.map(segment => segment.text) : [rejoined];
	const keptTexts = kept.map(segment => segment.text);
	if (texts.length !== keptTexts.length || texts.some((text, index) => text !== keptTexts[index])) return undefined;

	if (refused(rejoined)) return undefined;
	const cd = omp.leadingCd(rejoined);
	if (cd && refused(cd.rest)) return undefined;
	return rejoined;
}
