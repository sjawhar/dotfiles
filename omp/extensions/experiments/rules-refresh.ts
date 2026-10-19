// The rules refresh: omp reads a session's context files (~/.claude/CLAUDE.md, a repo's
// AGENTS.md, ...) once, into the system prompt, and keeps that copy for the life of the process;
// subagents inherit it. Before every prompt this feature compares each `<file path="...">` block
// of the system prompt with the file on disk, and when one has changed it adds one custom message
// (`rules-refresh`, shown in the transcript) naming each changed file, quoting its new or reworded
// paragraphs verbatim as in force now, and listing the start of each paragraph that is gone. A
// paragraph is a blank-line-separated chunk, compared trimmed, as a set. A file that no longer
// exists is ignored. Each disk version of a file is delivered once per session; the record resets
// at session_start, session_switch and session_branch. Disk reads are cheap: a file is re-read and
// re-hashed only when its mtime or size changed since the last look. Runs in top-level sessions and
// subagents alike, while the experiments extension's `rules_refresh` gate is on (index.ts).
import * as fs from "node:fs/promises";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export const CUSTOM_TYPE = "rules-refresh";
const REMOVED_PREVIEW = 100;

export type ContextBlock = { path: string; content: string };
export type DiskFile = { text: string; hash: string };
export type FileChange = { path: string; added: string[]; removed: string[]; hash: string };

const BLOCK = /<file path="([^"]+)">\n([\s\S]*?)\n<\/file>/g;

/** Every context-file block in the joined system prompt, in order; a path repeated keeps its first block. */
export function parseContextBlocks(systemPrompt: string): ContextBlock[] {
	const seen = new Set<string>();
	const blocks: ContextBlock[] = [];
	for (const [, path, content] of systemPrompt.matchAll(BLOCK)) {
		if (seen.has(path)) continue;
		seen.add(path);
		blocks.push({ path, content });
	}
	return blocks;
}

const paragraphs = (text: string) =>
	text
		.split(/\n[ \t]*\n/)
		.map(p => p.trim())
		.filter(p => p.length > 0);

/** Paragraphs of `now` absent from `old` (added or reworded), and paragraphs of `old` absent from `now`. */
export function paragraphDiff(old: string, now: string): { added: string[]; removed: string[] } {
	const before = paragraphs(old);
	const after = paragraphs(now);
	const beforeSet = new Set(before);
	const afterSet = new Set(after);
	return { added: after.filter(p => !beforeSet.has(p)), removed: before.filter(p => !afterSet.has(p)) };
}

/** The notice for a set of changed files. */
export function renderNotice(changes: FileChange[]): string {
	const parts = [
		"Context files changed on disk after this session loaded them. For each file below, the paragraphs quoted are new or reworded and are in force now, replacing the wording loaded at session start; the paragraphs listed as removed no longer apply.",
	];
	for (const change of changes) {
		parts.push(`## ${change.path}`);
		if (change.added.length > 0) parts.push("New or reworded paragraphs, in force now:", ...change.added);
		if (change.removed.length > 0) {
			const preview = (p: string) => {
				const line = p.replace(/\s+/g, " ");
				return line.length > REMOVED_PREVIEW ? `${line.slice(0, REMOVED_PREVIEW)}…` : line;
			};
			parts.push(`Removed paragraphs:\n${change.removed.map(p => `- ${preview(p)}`).join("\n")}`);
		}
	}
	return parts.join("\n\n");
}

/**
 * The per-session refresh state. `read` returns a file's current text and hash, or undefined when
 * it does not exist. `check` returns the notice for the files whose disk version differs from
 * their block and has not been delivered yet, or undefined.
 */
export function createRefresher(read: (path: string) => Promise<DiskFile | undefined>) {
	const delivered = new Set<string>();
	return {
		reset: () => delivered.clear(),
		async check(systemPrompt: string[]): Promise<string | undefined> {
			const changes: FileChange[] = [];
			for (const block of parseContextBlocks(systemPrompt.join("\n"))) {
				const disk = await read(block.path);
				if (!disk || disk.text.trim() === block.content.trim()) continue;
				const key = `${block.path}\0${disk.hash}`;
				if (delivered.has(key)) continue;
				const { added, removed } = paragraphDiff(block.content, disk.text);
				if (added.length === 0 && removed.length === 0) continue;
				delivered.add(key);
				changes.push({ path: block.path, added, removed, hash: disk.hash });
			}
			return changes.length > 0 ? renderNotice(changes) : undefined;
		},
	};
}

/** A reader that re-reads and re-hashes a file only when its mtime or size changed. */
export function cachedReader(): (path: string) => Promise<DiskFile | undefined> {
	const cache = new Map<string, { mtimeMs: number; size: number; file: DiskFile }>();
	return async path => {
		let stat: { mtimeMs: number; size: number; isFile(): boolean };
		try {
			stat = await fs.stat(path);
		} catch {
			cache.delete(path);
			return undefined;
		}
		if (!stat.isFile()) return undefined;
		const hit = cache.get(path);
		if (hit && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) return hit.file;
		const text = await Bun.file(path).text();
		const file = { text, hash: Bun.hash(text).toString(16) };
		cache.set(path, { mtimeMs: stat.mtimeMs, size: stat.size, file });
		return file;
	};
}

export default function rulesRefresh(pi: ExtensionAPI): void {
	const refresher = createRefresher(cachedReader());
	pi.on("session_start", () => refresher.reset());
	pi.on("session_switch", () => refresher.reset());
	pi.on("session_branch", () => refresher.reset());
	pi.on("before_agent_start", async (event: { systemPrompt: string[] }) => {
		const content = await refresher.check(event.systemPrompt);
		return content ? { message: { customType: CUSTOM_TYPE, content, display: true } } : undefined;
	});
}
