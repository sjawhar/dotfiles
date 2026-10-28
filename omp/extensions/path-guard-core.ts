// The path guard's logic (path-guard.ts binds it; its header says what the guard is for and what it
// does not see). It finds the paths a tool call writes or reads, resolves them against the session
// cwd, and refuses the call when one falls outside the directories SCHED_ALLOW_WRITE (and, when set,
// SCHED_ALLOW_READ) name.
//
// A `bash` command is read by a small shell lexer: quotes, escapes, `$VAR`/`${VAR}` from the
// environment, `~`, comments, heredoc bodies (skipped), `$(…)` and `( … )` (their commands are read
// too; a `cd` inside a subshell ends with it), `((…))` and `$((…))` arithmetic (skipped), and the
// operators that split commands. A target the lexer cannot know — an unknown variable, a backtick
// or `$(…)` substitution, a relative path after a `cd` to an unknown place — is let through.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export type Env = Record<string, string | undefined>;
/** The raw targets a `write`, `edit` or `ast_edit` call names (running-script-guard.ts's `targetsOf`). */
export type EditTargets = (toolName: string, input: Record<string, unknown>) => string[];

type CallEvent = { toolName: string; input: Record<string, unknown> };
type Ctx = { cwd: string };
/** A list's directories as the refusal names them, and with each one's real path added, for matching. */
type DirList = { shown: string[]; match: string[] };
type Config = { write?: DirList; read?: DirList; env: Env };

type Word = { kind: "word"; value: string; unknown: boolean; glob: boolean; quoted: boolean };
type Token = Word | { kind: "op"; op: string } | { kind: "redir"; op: string };
type Option = { name: string; value?: Word };
type Found = { writes: Word[]; reads: Word[] };

const URL_SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;
const GLOB_CHARS = /[*?[{]/;
const DEVICES = /^\/dev\/(?:null|zero|stdin|stdout|stderr|tty|fd\/\d+)$|^\/proc\/self\/fd\/\d+$/;
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*\+?=/;
const NAME_START = /[A-Za-z_]/;
const NAME_CHAR = /[A-Za-z0-9_]/;
const MAX_SHOWN = 5;
/** Words that come before a command's name without being it. */
const RESERVED: Record<string, true> = { "!": true, "{": true, "}": true, if: true, then: true, else: true, elif: true, fi: true, do: true, done: true, while: true, until: true, time: true };
/** Commands that run the next word as a command; their own options are skipped. */
const WRAPPERS: Record<string, true> = { sudo: true, env: true, command: true, builtin: true, exec: true, nohup: true, nice: true };

const none: Found = { writes: [], reads: [] };
const allWritten = (operands: Word[]): Found => ({ writes: operands, reads: [] });
const allRead = (operands: Word[]): Found => ({ writes: [], reads: operands });
/** `-t DIR` / `--target-directory=DIR` of cp and mv. */
const targetDir = (options: Option[]) => options.find(o => o.name === "-t" || o.name === "--target-directory")?.value;

/** The commands whose operands the guard knows, and their options that take a value. */
const COMMANDS: Record<string, { withValue: string[]; targets(operands: Word[], options: Option[]): Found }> = {
	tee: { withValue: [], targets: allWritten },
	rm: { withValue: [], targets: allWritten },
	rmdir: { withValue: [], targets: allWritten },
	touch: { withValue: ["-d", "-r", "-t", "--date", "--reference"], targets: allWritten },
	mkdir: { withValue: ["-m", "--mode", "--context"], targets: allWritten },
	cp: {
		withValue: ["-t", "-S", "--target-directory", "--suffix"],
		targets: (operands, options) => {
			const dir = targetDir(options);
			if (dir) return { writes: [dir], reads: operands };
			return operands.length >= 2 ? { writes: operands.slice(-1), reads: operands.slice(0, -1) } : none;
		},
	},
	// A move removes its sources, so they are written too.
	mv: {
		withValue: ["-t", "-S", "--target-directory", "--suffix"],
		targets: (operands, options) => {
			const dir = targetDir(options);
			return allWritten(dir ? [dir, ...operands] : operands);
		},
	},
	sed: {
		withValue: ["-e", "-f", "-l", "--expression", "--file", "--line-length"],
		targets: (operands, options) => {
			if (!options.some(o => o.name === "-i" || o.name === "--in-place")) return none;
			const scripted = options.some(o => ["-e", "-f", "--expression", "--file"].includes(o.name));
			return allWritten(scripted ? operands : operands.slice(1));
		},
	},
	cat: { withValue: [], targets: allRead },
	head: { withValue: ["-n", "-c", "--lines", "--bytes"], targets: allRead },
	tail: { withValue: ["-n", "-c", "--lines", "--bytes", "--pid", "-s", "--sleep-interval"], targets: allRead },
};

const unique = <T>(items: T[]): T[] => [...new Set(items)];
const home = (env: Env): string => env.HOME || os.homedir();

function expandHome(p: string, env: Env): string {
	return p === "~" || p.startsWith("~/") ? path.join(home(env), p.slice(1)) : p;
}

/** A glob's fixed directory part: `src/**\/*.ts` is `src/`, `*.ts` is `.`. */
function globBase(p: string): string {
	const at = p.search(GLOB_CHARS);
	if (at < 0) return p;
	const fixed = p.slice(0, at);
	return fixed.endsWith("/") ? fixed : path.dirname(fixed);
}

/** `p` with its symlinks resolved as far as it exists; the missing rest is joined on unchanged. */
function realish(p: string): string {
	let head = p;
	const tail: string[] = [];
	for (;;) {
		try {
			return path.join(fs.realpathSync(head), ...tail);
		} catch {
			const parent = path.dirname(head);
			if (parent === head) return p;
			tail.unshift(path.basename(head));
			head = parent;
		}
	}
}

function within(p: string, dir: string): boolean {
	return dir === "/" || p === dir || p.startsWith(`${dir}/`);
}

/** Whether an absolute path is inside the list, as written or through its symlinks. */
function allowedIn(p: string, list: DirList): boolean {
	if (DEVICES.test(p) || list.match.some(dir => within(p, dir))) return true;
	const real = realish(p);
	return real !== p && list.match.some(dir => within(real, dir));
}

function dirList(value: string | undefined, env: Env, base: string): DirList | undefined {
	const shown = unique(
		(value ?? "")
			.split(":")
			.map(entry => entry.trim())
			.filter(Boolean)
			.map(entry => path.resolve(base, expandHome(entry, env))),
	);
	if (shown.length === 0) return undefined;
	return { shown, match: unique([...shown, ...shown.map(realish)]) };
}

/**
 * A tool's path string, outside the list: its absolute form for the refusal, or undefined when it is
 * inside or a URL. It is inside when any of its forms is: as written, without a `?query`, or without a
 * `:selector` or archive member.
 */
function outsideTool(raw: string, cwd: string, env: Env, list: DirList): string | undefined {
	const text = raw.trim();
	if (text === "" || URL_SCHEME.test(text)) return undefined;
	const forms = unique([text, text.split("?")[0], text.split(":")[0]])
		.filter(Boolean)
		.map(form => path.resolve(cwd, globBase(expandHome(form, env))));
	return forms.some(p => allowedIn(p, list)) ? undefined : forms[forms.length - 1];
}

/** Splits a bash command into words, operators (`;`, `&&`, `|`, `(`, newline, …) and redirections. */
export function lexBash(command: string, env: Env): Token[] {
	const tokens: Token[] = [];
	const n = command.length;
	let word: Word | undefined;
	/** Set after `<<` (false) or `<<-` (true, strips tabs) until its delimiter word ends. */
	let delimiterFor: boolean | undefined;
	const heredocs: { delimiter: string; stripTabs: boolean }[] = [];

	const start = (): Word => {
		word ??= { kind: "word", value: "", unknown: false, glob: false, quoted: false };
		return word;
	};
	const end = () => {
		if (!word) return;
		tokens.push(word);
		if (delimiterFor !== undefined) heredocs.push({ delimiter: word.value, stripTabs: delimiterFor });
		delimiterFor = undefined;
		word = undefined;
	};
	const op = (text: string) => {
		end();
		tokens.push({ kind: "op", op: text });
	};
	/** The index of the `)` that closes the `(` at `open`. */
	const closeParen = (open: number): number => {
		let depth = 0;
		for (let i = open; i < n; i++) {
			if (command[i] === "(") depth++;
			else if (command[i] === ")" && --depth === 0) return i;
		}
		return n;
	};
	/** After the last newline, the end of the heredoc bodies it starts. */
	const skipHeredocs = (from: number): number => {
		let pos = from;
		for (const { delimiter, stripTabs } of heredocs.splice(0)) {
			while (pos < n) {
				const newline = command.indexOf("\n", pos);
				const line = command.slice(pos, newline < 0 ? n : newline);
				pos = newline < 0 ? n : newline + 1;
				if ((stripTabs ? line.replace(/^\t+/, "") : line) === delimiter) break;
			}
		}
		return pos;
	};
	/** Expands the `$` at `i` into `w`; returns the index of the expansion's last character. */
	const dollar = (i: number, w: Word, inDouble: boolean): number => {
		const next = command[i + 1];
		if (next === "(") {
			w.unknown = true;
			// Arithmetic, or a substitution inside quotes, is skipped; a bare one's commands are read.
			if (command[i + 2] === "(" || inDouble) return closeParen(i + 1);
			op("(");
			return i + 1;
		}
		if (next === "{") {
			const close = command.indexOf("}", i + 2);
			if (close < 0) {
				w.unknown = true;
				return n;
			}
			const name = command.slice(i + 2, close);
			const value = /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? env[name] : undefined;
			if (value === undefined) w.unknown = true;
			else w.value += value;
			return close;
		}
		if (next !== undefined && NAME_START.test(next)) {
			let j = i + 1;
			while (j < n && NAME_CHAR.test(command[j])) j++;
			const value = env[command.slice(i + 1, j)];
			if (value === undefined) w.unknown = true;
			else w.value += value;
			return j - 1;
		}
		if (next !== undefined && /[0-9@*#?$!-]/.test(next)) {
			w.unknown = true;
			return i + 1;
		}
		if (next === "'" && !inDouble) {
			w.unknown = true;
			const close = command.indexOf("'", i + 2);
			return close < 0 ? n : close;
		}
		w.value += "$";
		return i;
	};
	const backtick = (i: number, w: Word): number => {
		w.unknown = true;
		const close = command.indexOf("`", i + 1);
		return close < 0 ? n : close;
	};
	/** Reads a double-quoted string from `from`; returns the index of its closing quote. */
	const double = (from: number, w: Word): number => {
		for (let i = from; i < n; i++) {
			const ch = command[i];
			if (ch === '"') return i;
			if (ch === "\\" && i + 1 < n && '"\\$`\n'.includes(command[i + 1])) {
				if (command[i + 1] !== "\n") w.value += command[i + 1];
				i++;
			} else if (ch === "$") i = dollar(i, w, true);
			else if (ch === "`") i = backtick(i, w);
			else w.value += ch;
		}
		return n;
	};

	for (let i = 0; i < n; i++) {
		const ch = command[i];
		const next = command[i + 1];
		if (ch === " " || ch === "\t" || ch === "\r") {
			end();
		} else if (ch === "\n") {
			op("\n");
			i = skipHeredocs(i + 1) - 1;
		} else if (ch === "#" && !word) {
			while (i + 1 < n && command[i + 1] !== "\n") i++;
		} else if (ch === "\\" && next === "\n") {
			i++;
		} else if (ch === ";" || ch === ")") {
			op(ch);
		} else if (ch === "(") {
			if (next === "(" && !word) {
				end();
				i = closeParen(i);
			} else op("(");
		} else if (ch === "|") {
			const text = next === "|" || next === "&" ? ch + next : ch;
			op(text);
			i += text.length - 1;
		} else if (ch === "&" && next === ">") {
			const text = command[i + 2] === ">" ? "&>>" : "&>";
			end();
			tokens.push({ kind: "redir", op: text });
			i += text.length - 1;
		} else if (ch === "&") {
			const text = next === "&" ? "&&" : "&";
			op(text);
			i += text.length - 1;
		} else if (ch === "<" || ch === ">") {
			// A run of digits right before the operator is the fd it redirects (`2>`), not a word.
			if (word && !word.quoted && !word.unknown && /^\d+$/.test(word.value)) word = undefined;
			else end();
			if (next === "(") {
				// Process substitution: its commands are read, and it names no file.
				op("(");
				i++;
				continue;
			}
			let text: string = ch;
			if (ch === ">" && (next === ">" || next === "|" || next === "&")) text += next;
			else if (ch === "<" && next === "<") text = command[i + 2] === "<" ? "<<<" : command[i + 2] === "-" ? "<<-" : "<<";
			else if (ch === "<" && (next === "&" || next === ">")) text += next;
			tokens.push({ kind: "redir", op: text });
			if (text === "<<" || text === "<<-") delimiterFor = text === "<<-";
			i += text.length - 1;
		} else {
			const w = start();
			if (ch === "'") {
				w.quoted = true;
				const close = command.indexOf("'", i + 1);
				w.value += command.slice(i + 1, close < 0 ? n : close);
				i = close < 0 ? n : close;
			} else if (ch === '"') {
				w.quoted = true;
				i = double(i + 1, w);
			} else if (ch === "\\") {
				w.quoted = true;
				if (next !== undefined) w.value += next;
				i++;
			} else if (ch === "$") {
				i = dollar(i, w, false);
			} else if (ch === "`") {
				i = backtick(i, w);
			} else if (ch === "~" && w.value === "" && !w.quoted && (next === undefined || /[/\s;&|()<>]/.test(next))) {
				w.value += home(env);
			} else {
				if (GLOB_CHARS.test(ch)) w.glob = true;
				w.value += ch;
			}
		}
	}
	end();
	return tokens;
}

/** A command's operands and options, getopt-style: `-tDIR`, `-t DIR`, `--opt=V`, `--opt V`; all operands after `--`. */
function parseArgs(args: Word[], withValue: string[]): { operands: Word[]; options: Option[] } {
	const operands: Word[] = [];
	const options: Option[] = [];
	for (let k = 0; k < args.length; k++) {
		const arg = args[k];
		const v = arg.value;
		if (v === "--") {
			operands.push(...args.slice(k + 1));
			break;
		}
		if (v.startsWith("--")) {
			const eq = v.indexOf("=");
			const name = eq < 0 ? v : v.slice(0, eq);
			if (eq >= 0) options.push({ name, value: { ...arg, value: v.slice(eq + 1) } });
			else if (withValue.includes(name)) options.push({ name, value: args[++k] });
			else options.push({ name });
		} else if (v.startsWith("-") && v.length > 1) {
			for (let j = 1; j < v.length; j++) {
				const name = `-${v[j]}`;
				if (!withValue.includes(name)) {
					options.push({ name });
					continue;
				}
				options.push({ name, value: j + 1 < v.length ? { ...arg, value: v.slice(j + 1) } : args[++k] });
				break;
			}
		} else {
			operands.push(arg);
		}
	}
	return { operands, options };
}

/** The absolute path a word names, or undefined when it cannot be known; a glob becomes its fixed directory. */
function wordPath(w: Word, dir: string | undefined): string | undefined {
	if (w.unknown || w.value === "" || w.value === "-" || URL_SCHEME.test(w.value)) return undefined;
	const p = w.glob ? globBase(w.value) : w.value;
	if (path.isAbsolute(p)) return path.resolve(p);
	return dir === undefined ? undefined : path.resolve(dir, p);
}

/** The absolute paths a bash command writes and reads, as far as the guard can tell. */
export function bashTargets(command: string, cwd: string | undefined, env: Env): { writes: string[]; reads: string[] } {
	const writes: string[] = [];
	const reads: string[] = [];
	const saved: (string | undefined)[] = [];
	let dir = cwd;
	let segment: Token[] = [];
	const add = (into: string[], words: Word[]) => {
		for (const w of words) {
			const p = wordPath(w, dir);
			if (p) into.push(p);
		}
	};

	const flush = () => {
		const tokens = segment;
		segment = [];
		const words: Word[] = [];
		// In `[[ a > b ]]`, `<` and `>` compare strings.
		const test = tokens.some(t => t.kind === "word" && t.value === "[[");
		for (let k = 0; k < tokens.length; k++) {
			const t = tokens[k];
			if (t.kind === "word") {
				words.push(t);
				continue;
			}
			if (t.kind !== "redir") continue;
			const target = tokens[k + 1]?.kind === "word" ? (tokens[k + 1] as Word) : undefined;
			if (!target) continue;
			k++;
			if (test || t.op.startsWith("<<") || t.op === "<&") continue;
			if (t.op === ">&" && /^(\d+-?|-)$/.test(target.value)) continue;
			add(t.op === "<" ? reads : writes, [target]);
		}

		let k = 0;
		while (k < words.length) {
			const v = words[k].value;
			if (Object.hasOwn(RESERVED, v) || ASSIGNMENT.test(v)) k++;
			else if (Object.hasOwn(WRAPPERS, v)) {
				k++;
				while (k < words.length && words[k].value.startsWith("-")) k++;
			} else break;
		}
		if (k >= words.length) return;
		const name = path.basename(words[k].value);
		const args = words.slice(k + 1);
		if (name === "cd" || name === "pushd") {
			const target = args.find(a => !a.value.startsWith("-") || a.value === "-");
			dir = !target ? home(env) : target.value === "-" ? undefined : wordPath(target, dir);
			return;
		}
		if (!Object.hasOwn(COMMANDS, name)) return;
		const rule = COMMANDS[name];
		const { operands, options } = parseArgs(args, rule.withValue);
		const found = rule.targets(operands, options);
		add(writes, found.writes);
		add(reads, found.reads);
	};

	for (const token of lexBash(command, env)) {
		if (token.kind !== "op") {
			segment.push(token);
			continue;
		}
		flush();
		if (token.op === "(") saved.push(dir);
		else if (token.op === ")" && saved.length > 0) dir = saved.pop();
	}
	flush();
	return { writes: unique(writes), reads: unique(reads) };
}

function shownList(paths: string[]): string {
	const more = paths.length - MAX_SHOWN;
	return paths.slice(0, MAX_SHOWN).join(", ") + (more > 0 ? ` and ${more} more` : "");
}

function refusal(toolName: string, writes: string[], reads: string[], config: Config): string | undefined {
	const lines: string[] = [];
	if (writes.length > 0 && config.write) {
		lines.push(`It would write ${shownList(writes)}, outside the directories this session may write (SCHED_ALLOW_WRITE): ${config.write.shown.join(", ")}.`);
	}
	if (reads.length > 0 && config.read) {
		lines.push(`It would read ${shownList(reads)}, outside the directories this session may read (SCHED_ALLOW_READ and SCHED_ALLOW_WRITE): ${config.read.shown.join(", ")}.`);
	}
	if (lines.length === 0) return undefined;
	return [`Path guard: this ${toolName} call was refused; nothing in it ran.`, ...lines, "Keep this step's work inside those directories."].join("\n");
}

function evaluate(event: CallEvent, cwd: string, config: Config, editTargets: EditTargets): string | undefined {
	const { toolName, input } = event;
	const { write, read, env } = config;
	const writes: string[] = [];
	const reads: string[] = [];
	// eval runs code whose writes cannot be read from the call; a haiku step agent under a write
	// list used it to write outside the list in the guard's first live run (2026-10-28).
	if (toolName === "eval" && write) {
		return [
			"Path guard: this eval call was refused; nothing in it ran.",
			`eval runs code whose file writes the guard cannot check, and this session may write only inside: ${write.shown.join(", ")} (SCHED_ALLOW_WRITE).`,
			"Change files with write, edit or bash instead.",
		].join("\n");
	}
	if (toolName === "write" || toolName === "edit" || toolName === "ast_edit") {
		if (!write) return undefined;
		for (const raw of editTargets(toolName, input)) {
			const outside = outsideTool(raw, cwd, env, write);
			if (outside) writes.push(outside);
		}
	} else if (toolName === "read" || toolName === "grep" || toolName === "glob" || toolName === "ast_grep") {
		if (!read) return undefined;
		const given = typeof input.path === "string" && input.path.trim() !== "" ? input.path : undefined;
		// read requires a path; the search tools search the cwd without one.
		const raw = given ?? (toolName === "read" ? "" : ".");
		for (const part of raw.split(";")) {
			const text = part.trim();
			if (text === "" || text.startsWith("!")) continue;
			const outside = outsideTool(text, cwd, env, read);
			if (outside) reads.push(outside);
		}
	} else if (toolName === "bash" && typeof input.command === "string") {
		const given = typeof input.cwd === "string" ? input.cwd.trim() : "";
		const dir = given === "" ? cwd : URL_SCHEME.test(given) ? undefined : path.resolve(cwd, expandHome(given, env));
		const found = bashTargets(input.command, dir, env);
		if (write) writes.push(...found.writes.filter(p => !allowedIn(p, write)));
		if (read) reads.push(...found.reads.filter(p => !allowedIn(p, read)));
	}
	return refusal(toolName, unique(writes), unique(reads), config);
}

/**
 * The guard for `env`; it registers nothing when neither list is set. `launchCwd` resolves a relative
 * list entry.
 */
export function createPathGuard(env: Env, editTargets: EditTargets, launchCwd: string = process.cwd()): (pi: ExtensionAPI) => void {
	const write = dirList(env.SCHED_ALLOW_WRITE, env, launchCwd);
	const readList = dirList(env.SCHED_ALLOW_READ, env, launchCwd);
	// What a session may write, it may read.
	const read = readList && write ? { shown: unique([...readList.shown, ...write.shown]), match: unique([...readList.match, ...write.match]) } : readList;
	return pi => {
		if (!write && !read) return;
		const config: Config = { write, read, env };
		pi.on("tool_call", (event: CallEvent, ctx: Ctx) => {
			try {
				const reason = evaluate(event, ctx.cwd, config, editTargets);
				return reason === undefined ? undefined : { block: true, reason };
			} catch (error) {
				pi.logger.debug("path-guard: check skipped", { tool: event.toolName, error: error instanceof Error ? error.message : String(error) });
				return undefined;
			}
		});
	};
}
