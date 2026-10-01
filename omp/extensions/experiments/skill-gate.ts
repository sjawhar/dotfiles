// The skill gate: two commands are refused until the skill they need is in the session's context.
//
//   gh pr create  (or gh's alias `gh pr new`, gh's global flags allowed before `pr`, or the same
//                 through `legion gh --`) until skill://opening-a-pr has been read;
//   jj git push   (jj's global flags allowed before `git`) until skill://using-jj has been read.
//
// Either counts from a bash call's command or from eval code that shells out. The refusal names
// the skill to read. It runs in top-level sessions and subagents alike, while the experiments
// extension's `skill_gate` gate is on (index.ts). Deterministic: regexes over the tool call and a
// scan of the session branch, no model call, no session entries of its own.
//
// A skill is in context when, on the branch from its lower bound, a `skill-prompt` custom message
// names it (a /skill: prompt or an autoloaded skill) or a `read skill://<name>` (or
// `skill://<name>/SKILL.md`) call has a non-error result. The lower bound is the newest boundary:
// the entry after the latest reset (/clear), or the latest compaction's first kept entry. A read in
// the same assistant message as the gated call has no result yet when the call is checked, so it
// does not count; the refusal says so.
//
// Eval code shells out when it uses a shell API (subprocess and asyncio's create_subprocess_*,
// os.system and the like, child_process, Bun.$ and Bun.spawn) or, in a Python cell, an IPython
// escape (`!cmd`, `out = !cmd`, a %%bash or %%sh cell). There a command counts only in command
// position: a string that opens a call argument, a list's first item or an assignment's value;
// after && || ; | or $(; after an IPython `!` in Python or a shell template in JavaScript; and at a
// line start in a %%bash cell or a multi-line string. In Python a backtick opens no string. Words
// that run the command after them may come first (sudo, env, VAR=value, timeout, nice, nohup, time,
// a shell's -c, secrets … --, do, then and the like), and the command may be named by its path. A
// helper an earlier cell defined, or a script a write created and a later call runs, is not read.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

type Input = Record<string, unknown>;
type Entry = { type: string; id?: string; [key: string]: unknown };
type CallEvent = { toolName: string; input: Input };
type Ctx = { sessionManager: { getBranch: () => readonly Entry[] } };

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const SKILL_READ = /^skill:\/\/([^/\s?#]+)(?:\/SKILL\.md)?$/;

// eval code that shells out: a shell API in the code (Python's bare `exec(` runs code, not a shell), or in a Python cell
// an IPython shell escape (`!cmd`, `out = !cmd`, a `%%bash` or `%%sh` cell).
const SHELL_API = /\bsubprocess\b|\bcreate_subprocess_(?:exec|shell)\b|\bos\.(?:system|popen|spawn\w*|exec\w*)\b|\bchild_process\b|\b(?:execSync|execFileSync|execFile|spawnSync|spawn)\s*\(|\bBun\.(?:\$|spawn\w*)|\$`/;
// An IPython shell escape at a line start: `!cmd`, or `out = !cmd`.
const IPY_ESCAPE = String.raw`^[ \t]*(?:[\w.]+[ \t]*=[ \t]*)?!`;
const PY_SHELL_ESCAPE = new RegExp(String.raw`${IPY_ESCAPE}|^[ \t]*%%(?:bash|sh)\b`, "m");
const SHELL_CELL = /^\s*%%(?:bash|sh)\b/;
// A string literal's opening quote: Python's, with its prefixes, and JavaScript's, a template's backtick among them.
const STRING_OPEN_PY = String.raw`[rRbBuUfF]{0,2}["']`;
const STRING_OPEN_JS = String.raw`["'\x60]`;
/** What separates a command's words in eval code, as a character-class body: spaces inside one string, or the quotes and commas between list items. */
const EVAL_SPACE = String.raw`\s"',`;
/**
 * Words that run the command after them, any number of them in a row: sudo, env and `VAR=value` assignments, timeout,
 * nice, nohup, time, exec, command, a shell's `-c` (`bash -c`, `sh -lc`), `secrets … --` and `secret-run … --`, and the
 * shell keywords that open a command (do, then, else, if, while, until, !). Each word sequence parses one way only
 * (env takes its flags and the assignments after it are wrappers of their own), so a failed match never backtracks
 * through the ways to split it.
 */
const COMMAND_PREFIX = (() => {
	const sep = `[${EVAL_SPACE}]+`;
	const word = `[^${EVAL_SPACE}]`;
	const wrappers = [
		String.raw`sudo(?:${sep}(?:-u${sep}${word}+|-[A-Za-tv-z]+))*`,
		String.raw`env(?:${sep}-${word}+)*`,
		String.raw`[A-Za-z_]\w*=${word}*`,
		String.raw`timeout(?:${sep}(?:-${word}+|\d+(?:\.\d+)?[smhd]?))+`,
		String.raw`nice(?:${sep}-n${sep}?-?\d+|${sep}-\d+|${sep}--adjustment=-?\d+)?`,
		"nohup",
		"time",
		"exec",
		"command",
		String.raw`(?:ba|z|da|k)?sh(?:${sep}-[a-z]+)*?${sep}-[a-z]*c`,
		String.raw`secret(?:s|-run)(?:${sep}(?!--(?!${word}))${word}+)*?${sep}--`,
		"do",
		"then",
		"else",
		"if",
		"while",
		"until",
		"!",
	];
	return `(?:(?:${wrappers.join("|")})${sep})*`;
})();
// A command named by its path (`/usr/bin/git`, `~/.local/bin/jj`): the directory before the command's word.
const COMMAND_PATH = String.raw`(?:[^${EVAL_SPACE};&|()<>]*\/)?`;
// Where a command's first word sits in eval code: a string that opens a call argument, a list or an assignment (the
// first item only, so the `git` of `["jj", "git", "push"]` is not a command), after && || ; | or $(, after an IPython
// `!` in Python or a Bun shell template in JavaScript; then any COMMAND_PREFIX words. Prose that names a command in a
// comment or a message body is not in command position. Any language but Python reads as JavaScript.
const commandStart = (language: string) =>
	language === "py"
		? String.raw`(?:\([ \t]*${STRING_OPEN_PY}|\[[ \t]*${STRING_OPEN_PY}|=[ \t]*${STRING_OPEN_PY}|&&|\|\||[;|]|\$\(|${IPY_ESCAPE})[ \t]*${COMMAND_PREFIX}`
		: String.raw`(?:\([ \t]*${STRING_OPEN_JS}|\[[ \t]*${STRING_OPEN_JS}|=[ \t]*${STRING_OPEN_JS}|&&|\|\||[;|]|\$\(|\$\x60)[ \t]*${COMMAND_PREFIX}`;

/**
 * The bodies of the strings in `code` that span lines: Python triple-quoted strings and JavaScript templates (Bun's
 * $`…` included). One pass: a string ends at its unescaped closing quote, or at the end of the code.
 */
function* multilineStrings(code: string, language: string): Generator<string> {
	for (let i = 0; i < code.length; i++) {
		const c = code[i];
		if (c === "\\") {
			i++;
			continue;
		}
		const quote = c === "`" && language !== "py" ? c : (c === '"' || c === "'") && code.startsWith(c.repeat(3), i) ? c.repeat(3) : undefined;
		if (!quote) continue;
		let end = i + quote.length;
		while (end < code.length && !code.startsWith(quote, end)) end += code[end] === "\\" ? 2 : 1;
		const body = code.slice(i + quote.length, end);
		if (body.includes("\n")) yield body;
		i = end + quote.length - 1;
	}
}

/** A test that eval code which shells out runs a command whose words `words` (a regex source) matches in command position. */
function evalRuns(words: string): (code: string, language: string) => boolean {
	const anywhere = { py: new RegExp(commandStart("py") + COMMAND_PATH + words, "m"), js: new RegExp(commandStart("js") + COMMAND_PATH + words, "m") };
	const lineStart = new RegExp(String.raw`^[ \t]*${COMMAND_PREFIX}${COMMAND_PATH}${words}`, "m");
	return (code, language) => {
		if (!SHELL_API.test(code) && !(language === "py" && PY_SHELL_ESCAPE.test(code))) return false;
		if ((language === "py" ? anywhere.py : anywhere.js).test(code)) return true;
		for (const body of multilineStrings(code, language)) if (lineStart.test(body)) return true;
		return SHELL_CELL.test(code) && lineStart.test(code);
	};
}

/** A command's leading word, then its global flags (each with at most one value), then `rest`; `space` is the character-class body of what separates the words. */
const withFlags = (command: string, space: string, rest: string) =>
	String.raw`${command}(?:[${space}]+-{1,2}[\w-]+(?:[${space}=]+[^${space}-][^${space}]*)?)*[${space}]+${rest}`;
const prCreate = (space: string) => String.raw`(?:${withFlags("gh", space, "pr")}|legion[${space}]+gh[${space}]+--[${space}]+pr)[${space}]+(?:create|new)\b`;
const jjGitPush = (space: string) => withFlags("jj", space, String.raw`git[${space}]+push\b`);

interface Refusal {
	skill: string;
	bash: RegExp;
	evalCode: (code: string, language: string) => boolean;
}
export const REFUSALS: readonly Refusal[] = [
	{ skill: "opening-a-pr", bash: new RegExp(String.raw`\b${prCreate(String.raw`\s`)}`), evalCode: evalRuns(prCreate(EVAL_SPACE)) },
	{ skill: "using-jj", bash: new RegExp(String.raw`\b${jjGitPush(String.raw`\s`)}`), evalCode: evalRuns(jjGitPush(EVAL_SPACE)) },
];

/** The skills a bash or eval call needs in context before it runs; empty for any other call. */
export function skillsNeeded(toolName: string, input: Input): string[] {
	if (toolName === "bash" && typeof input.command === "string") {
		const command = input.command;
		return REFUSALS.filter(r => r.bash.test(command)).map(r => r.skill);
	}
	if (toolName === "eval" && typeof input.code === "string") {
		const code = input.code;
		const language = typeof input.language === "string" ? input.language : "";
		return REFUSALS.filter(r => r.evalCode(code, language)).map(r => r.skill);
	}
	return [];
}

/** Index of the first branch entry the model still has: the entry after the latest reset, or the latest compaction's first kept entry (a compaction never keeps entries from before a reset); 0 when neither exists. */
export function contextLowerBound(branch: readonly Entry[]): number {
	for (let i = branch.length - 1; i >= 0; i--) {
		const entry = branch[i];
		if (entry.type === "reset_boundary") return i + 1;
		if (entry.type === "compaction") {
			const kept = entry.firstKeptEntryId;
			const keptIndex = typeof kept === "string" ? branch.findIndex(e => e.id === kept) : -1;
			return keptIndex >= 0 ? keptIndex : i + 1;
		}
	}
	return 0;
}

/** Skills whose text is in the model's context: completed `read skill://<name>` results and `skill-prompt` messages from the lower bound on. */
export function loadedSkills(branch: readonly Entry[]): Set<string> {
	const pending = new Map<string, string>();
	const loaded = new Set<string>();
	for (let i = contextLowerBound(branch); i < branch.length; i++) {
		const entry = branch[i];
		if (entry.type === "custom_message") {
			const details = entry.details;
			if (entry.customType === "skill-prompt" && isRecord(details) && typeof details.name === "string" && details.name) loaded.add(details.name);
			continue;
		}
		const message = entry.message;
		if (entry.type !== "message" || !isRecord(message)) continue;
		if (message.role === "assistant" && Array.isArray(message.content)) {
			for (const block of message.content) {
				if (!isRecord(block) || block.type !== "toolCall" || block.name !== "read" || typeof block.id !== "string") continue;
				const target = isRecord(block.arguments) && typeof block.arguments.path === "string" ? block.arguments.path : "";
				const match = SKILL_READ.exec(target);
				if (match) pending.set(block.id, match[1]);
			}
		} else if (message.role === "toolResult" && message.toolName === "read" && !message.isError && typeof message.toolCallId === "string") {
			const name = pending.get(message.toolCallId);
			if (name) loaded.add(name);
		}
	}
	return loaded;
}

export default function skillGate(pi: ExtensionAPI): void {
	pi.on("tool_call", (event: CallEvent, ctx: Ctx) => {
		const needed = skillsNeeded(event.toolName, event.input);
		if (needed.length === 0) return undefined;
		const loaded = loadedSkills(ctx.sessionManager.getBranch());
		const missing = needed.filter(skill => !loaded.has(skill));
		if (missing.length === 0) return undefined;
		const skills = missing.map(skill => `skill://${skill}`).join(" and ");
		return {
			block: true,
			reason: `Skill gate: read ${skills} in a step of its own before this ${event.toolName} call (a read in the same batch does not count), then retry the call.`,
		};
	});
}
